"""
Evaluation harness runner.

Usage:
    python -m evals.run_eval                       # full dataset
    python -m evals.run_eval --category paraphrase # subset by category
    python -m evals.run_eval --case-id T01,L05     # specific cases
    python -m evals.run_eval --limit 10            # first N cases
    python -m evals.run_eval --skip-faithfulness   # skip LLM judge (offline)
    python -m evals.run_eval --concurrency 4       # parallel cases

Environment:
    AZURE_AI_PROJECT_ENDPOINT, AZURE_OPENAI_ENDPOINT, AZURE_SEARCH_ENDPOINT
    must be set and the user/MI must have RBAC roles assigned.
    EVAL_JUDGE_MODEL (optional) deployment name for the faithfulness judge.

Outputs:
    evals/results/eval_<timestamp>.json   raw per-case results
    evals/results/eval_<timestamp>.md     summary report
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# Make src/ importable when invoked as a script
_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_ROOT / "src"))

from evals.dataset import EVAL_CASES, EvalCase  # noqa: E402
from evals.metrics import (  # noqa: E402
    CitationResult,
    FaithfulnessResult,
    RecallResult,
    RefusalResult,
    compute_citation_accuracy,
    compute_recall,
    detect_refusal,
    score_faithfulness,
)

logger = logging.getLogger("evals")


@dataclass
class CaseResult:
    case_id: str
    category: str
    query: str
    expected_files: list[str]
    answer: str = ""
    matches: list[dict[str, Any]] = field(default_factory=list)
    recall: dict[str, Any] = field(default_factory=dict)
    citation: dict[str, Any] = field(default_factory=dict)
    refusal: dict[str, Any] = field(default_factory=dict)
    faithfulness: dict[str, Any] = field(default_factory=dict)
    latency_seconds: float = 0.0
    error: str | None = None


async def _run_one(
    case: EvalCase,
    orchestrator,
    skip_faithfulness: bool,
) -> CaseResult:
    result = CaseResult(
        case_id=case.case_id,
        category=case.category,
        query=case.query,
        expected_files=list(case.expected_files),
    )
    t0 = time.monotonic()
    try:
        out = await orchestrator.process_query_async(case.query)
        result.latency_seconds = time.monotonic() - t0

        result.answer = str(out.get("answer", "") or "")
        matches = out.get("matches") or []
        # When matches are not surfaced (single_agent mode), fall back to
        # activity-derived references if available.
        if not matches:
            for act in out.get("activity", []) or []:
                for ref in act.get("references", []) or []:
                    matches.append(
                        {
                            "content": ref.get("source_data", {}).get("snippet", ""),
                            "filePath": ref.get("source_data", {}).get(
                                "metadata_storage_path", ""
                            ),
                            "fileName": ref.get("source_data", {}).get(
                                "metadata_storage_name", ""
                            ),
                            "parentTitle": ref.get("source_data", {}).get(
                                "parent_title", ""
                            ),
                        }
                    )
        result.matches = matches

        recall: RecallResult = compute_recall(matches, case.expected_files)
        citation: CitationResult = compute_citation_accuracy(
            result.answer, case.expected_files
        )
        refusal: RefusalResult = detect_refusal(result.answer)

        if (
            case.faithfulness_required
            and not skip_faithfulness
            and not case.adversarial
        ):
            faith: FaithfulnessResult = await asyncio.to_thread(
                score_faithfulness, result.answer, matches
            )
        else:
            faith = FaithfulnessResult(0.0, "skipped", skipped=True)

        result.recall = asdict(recall)
        result.citation = asdict(citation)
        result.refusal = asdict(refusal)
        result.faithfulness = asdict(faith)
    except Exception as e:
        result.latency_seconds = time.monotonic() - t0
        result.error = f"{type(e).__name__}: {e}"
        logger.exception("Case %s failed: %s", case.case_id, e)
    return result


async def _run_all(
    cases: list[EvalCase],
    concurrency: int,
    skip_faithfulness: bool,
) -> list[CaseResult]:
    from agents.sequential_orchestrator_foundry import FoundryAgentOrchestrator

    orchestrator = FoundryAgentOrchestrator()
    semaphore = asyncio.Semaphore(concurrency)

    async def _bounded(case: EvalCase) -> CaseResult:
        async with semaphore:
            logger.info("[%s] %s", case.case_id, case.query[:80])
            return await _run_one(case, orchestrator, skip_faithfulness)

    return await asyncio.gather(*(_bounded(c) for c in cases))


def _aggregate(results: list[CaseResult]) -> dict[str, Any]:
    """Roll up per-case results into aggregate metrics, including per-category."""

    def _slice(rs: list[CaseResult]) -> dict[str, Any]:
        n = len(rs)
        if n == 0:
            return {"count": 0}
        nonadversarial = [r for r in rs if not (r.category == "adversarial")]
        recall_pool = [r for r in nonadversarial if r.recall.get("expected_files")]
        adv = [r for r in rs if r.category == "adversarial"]
        faith_pool = [
            r for r in rs if r.faithfulness and not r.faithfulness.get("skipped")
        ]
        cite_pool = [r for r in rs if r.recall.get("expected_files")]
        errors = [r for r in rs if r.error]

        def _mean(xs):
            return round(sum(xs) / len(xs), 4) if xs else 0.0

        return {
            "count": n,
            "errors": len(errors),
            "avg_latency_s": _mean([r.latency_seconds for r in rs]),
            "recall@1": _mean([r.recall.get("hit_at_1", 0) for r in recall_pool]),
            "recall@3": _mean([r.recall.get("hit_at_3", 0) for r in recall_pool]),
            "recall@5": _mean([r.recall.get("hit_at_5", 0) for r in recall_pool]),
            "mrr": _mean([r.recall.get("mrr", 0.0) for r in recall_pool]),
            "citation_present_rate": _mean(
                [int(r.citation.get("has_citation", False)) for r in cite_pool]
            ),
            "citation_accuracy": _mean(
                [int(r.citation.get("expected_cited", False)) for r in cite_pool]
            ),
            "faithfulness_avg": _mean(
                [r.faithfulness.get("score", 0.0) for r in faith_pool]
            ),
            "faithfulness_n": len(faith_pool),
            "adversarial_refusal_rate": (
                _mean([int(r.refusal.get("refused", False)) for r in adv])
                if adv
                else None
            ),
        }

    overall = _slice(results)
    by_cat: dict[str, Any] = {}
    cats = sorted({r.category for r in results})
    for cat in cats:
        by_cat[cat] = _slice([r for r in results if r.category == cat])
    return {"overall": overall, "by_category": by_cat}


def _write_reports(
    results: list[CaseResult], aggregate: dict[str, Any], out_dir: Path
) -> tuple[Path, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.utcnow().strftime("%Y%m%dT%H%M%SZ")
    json_path = out_dir / f"eval_{ts}.json"
    md_path = out_dir / f"eval_{ts}.md"

    json_path.write_text(
        json.dumps(
            {
                "timestamp_utc": ts,
                "aggregate": aggregate,
                "cases": [asdict(r) for r in results],
            },
            indent=2,
            default=str,
        )
    )

    # Markdown summary
    lines = [f"# Eval report — {ts}", ""]
    o = aggregate["overall"]
    lines += [
        "## Overall",
        "",
        f"- Cases: **{o['count']}** ({o['errors']} errors)",
        f"- Avg latency: **{o['avg_latency_s']:.2f}s**",
        f"- Recall@1 / @3 / @5: **{o['recall@1']:.2%}** / "
        f"**{o['recall@3']:.2%}** / **{o['recall@5']:.2%}**",
        f"- MRR: **{o['mrr']:.4f}**",
        f"- Citation present rate: **{o['citation_present_rate']:.2%}**",
        f"- Citation accuracy: **{o['citation_accuracy']:.2%}**",
        f"- Faithfulness avg ({o['faithfulness_n']} judged): "
        f"**{o['faithfulness_avg']:.3f}**",
    ]
    if o["adversarial_refusal_rate"] is not None:
        lines.append(
            f"- Adversarial refusal rate: **{o['adversarial_refusal_rate']:.2%}**"
        )
    lines += ["", "## By category", "",
              "| Category | N | R@1 | R@3 | R@5 | MRR | CiteAcc | Faith |",
              "|---|---|---|---|---|---|---|---|"]
    for cat, s in aggregate["by_category"].items():
        lines.append(
            f"| {cat} | {s['count']} | {s['recall@1']:.2%} | "
            f"{s['recall@3']:.2%} | {s['recall@5']:.2%} | "
            f"{s['mrr']:.3f} | {s['citation_accuracy']:.2%} | "
            f"{s['faithfulness_avg']:.3f} |"
        )

    failures = [
        r
        for r in results
        if r.error
        or (
            r.recall.get("expected_files") and r.recall.get("hit_at_5", 0) == 0
        )
    ]
    if failures:
        lines += ["", "## Failures (no expected file in top-5 or error)", ""]
        for r in failures[:25]:
            tag = "ERR" if r.error else "MISS"
            lines.append(f"- `{r.case_id}` [{tag}] {r.query}")
            if r.error:
                lines.append(f"    - {r.error}")
            elif r.matches:
                top = r.matches[0]
                got = top.get("fileName") or top.get("filePath") or "?"
                lines.append(f"    - top-1 returned: `{got}`")

    md_path.write_text("\n".join(lines))
    return json_path, md_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run eval harness")
    parser.add_argument("--category", help="Comma-separated category filter")
    parser.add_argument("--case-id", help="Comma-separated case ID filter")
    parser.add_argument("--limit", type=int, help="Run only the first N cases")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument(
        "--skip-faithfulness", action="store_true",
        help="Skip LLM-as-judge faithfulness scoring",
    )
    parser.add_argument(
        "--out-dir", default=str(_ROOT / "evals" / "results"),
        help="Directory for report outputs",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    cases = EVAL_CASES
    if args.category:
        wanted = {c.strip() for c in args.category.split(",")}
        cases = [c for c in cases if c.category in wanted]
    if args.case_id:
        wanted_ids = {c.strip() for c in args.case_id.split(",")}
        cases = [c for c in cases if c.case_id in wanted_ids]
    if args.limit:
        cases = cases[: args.limit]

    if not cases:
        print("No cases selected", file=sys.stderr)
        return 2

    logger.info(
        "Running %d cases (concurrency=%d, faithfulness=%s)",
        len(cases),
        args.concurrency,
        not args.skip_faithfulness,
    )

    results = asyncio.run(
        _run_all(cases, args.concurrency, args.skip_faithfulness)
    )
    aggregate = _aggregate(results)

    json_path, md_path = _write_reports(results, aggregate, Path(args.out_dir))
    print(json.dumps(aggregate, indent=2))
    print(f"\nWrote {json_path}")
    print(f"Wrote {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
