"""
Latency Investigation runner for Azure AI Foundry Agentic Retrieval.

Profiles the agentic-retrieval request path, breaks latency into
service-side (query planning / search / reranking / reasoning) and
client/agent overhead, aggregates percentiles across repeated runs, and
writes a Markdown + JSON report.

Usage
-----
    # Offline demo (no Azure needed) — synthetic but realistically shaped data
    python -m scripts.investigate_latency --mock --repeat 5

    # Live profiling against the deployed Foundry orchestrator
    python -m scripts.investigate_latency --repeat 3 --limit 8

    # Subset / single query
    python -m scripts.investigate_latency --mock --query "What is the PTO policy?"

Options
-------
    --mock           Generate synthetic runs offline (educational/demo).
    --repeat N       Runs per query (default 3). First run per query is
                     marked cold; the rest warm.
    --limit N        Use only the first N canonical queries.
    --query TEXT     Profile a single ad-hoc query (repeatable).
    --warmup         Discard one un-timed warmup run per query (live only).
    --output DIR     Report output directory (default: logs/latency).
    --seed N         RNG seed for --mock reproducibility (default 1234).

Environment (live mode)
-----------------------
    AZURE_AI_PROJECT_ENDPOINT and related search/OpenAI endpoints must be set,
    and the signed-in identity / MI must hold the required RBAC roles.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import sys
from pathlib import Path

# Make src/ importable when invoked as a script or module.
_ROOT = Path(__file__).resolve().parent.parent
_SRC = _ROOT / "src"
for _p in (_ROOT, _SRC):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from latency import (  # noqa: E402
    RunLatency,
    aggregate_runs,
    parse_activity,
    profile_orchestrator_call,
    render_markdown,
    save_report,
)
from latency.models import StageTiming  # noqa: E402

logger = logging.getLogger("latency.cli")

# A small, representative default query set (subset of the canonical
# tests/test_queries.py list) so the runner is useful even standalone.
_DEFAULT_QUERIES = [
    "What is the PTO policy?",
    "Find Policy 51350 on Paid Time Off",
    "Show me the probationary period requirements from Policy 50455",
    "What is the holiday pay policy?",
    "Where is the IT Acceptable Use Policy document?",
    "What is the Code of Ethics?",
    "Find the IT Information Security Policy 83100",
    "What are the pre-employment medical examination requirements?",
]


def _load_canonical_queries() -> list[str]:
    """Load the canonical query strings from tests/test_queries.py if present."""
    try:
        sys.path.insert(0, str(_ROOT / "tests"))
        from test_queries import TEST_QUERIES  # type: ignore

        return list(TEST_QUERIES)
    except Exception:  # noqa: BLE001
        return list(_DEFAULT_QUERIES)


# ---------------------------------------------------------------------------
# Mock (offline) run synthesis
# ---------------------------------------------------------------------------

def _mock_activity(rng: random.Random, n_subqueries: int) -> list[dict]:
    """Build a realistically shaped agentic-retrieval ``activity[]`` payload."""
    activity: list[dict] = [
        {
            "type": "modelQueryPlanning",
            "id": 0,
            "elapsedMs": rng.uniform(2800, 4200),
            "inputTokens": rng.randint(1500, 1900),
            "outputTokens": rng.randint(150, 260),
        }
    ]
    for i in range(1, n_subqueries + 1):
        activity.append(
            {
                "type": "searchIndex",
                "id": i,
                "knowledgeSourceName": "hr-knowledge-source",
                "elapsedMs": rng.uniform(60, 160),
                "count": rng.randint(4, 8),
            }
        )
    activity.append(
        {
            "type": "semanticReranker",
            "id": n_subqueries + 1,
            "elapsedMs": rng.uniform(180, 360),
        }
    )
    activity.append(
        {
            "type": "agenticReasoning",
            "id": n_subqueries + 2,
            "reasoningTokens": rng.randint(8000, 13000),
        }
    )
    return activity


def _mock_run(
    rng: random.Random,
    query: str,
    run_index: int,
    cold_start: bool,
) -> RunLatency:
    """Synthesize a single RunLatency with plausible stage + activity timings."""
    n_subqueries = rng.randint(3, 7)
    raw_activity = _mock_activity(rng, n_subqueries)
    activity = parse_activity(raw_activity)
    activity_total = sum(a.elapsed_ms for a in activity)

    # Client-side stages. Cold runs pay an agent-create + connection penalty.
    cold_penalty = rng.uniform(3500, 6500) if cold_start else 0.0
    client_setup = (rng.uniform(900, 1600) if cold_start else rng.uniform(120, 300))
    agent_ensure = (rng.uniform(1800, 2600) if cold_start else rng.uniform(120, 320))
    conv_create = rng.uniform(250, 600)
    # Agent streaming wraps the service activity plus LLM synthesis time.
    agent_response = activity_total + rng.uniform(4000, 9000) + cold_penalty
    # The extra direct agentic_retrieve made purely to capture activity[].
    activity_capture = activity_total + rng.uniform(50, 200)
    conv_delete = rng.uniform(120, 350)

    stages = [
        StageTiming("client_setup", client_setup),
        StageTiming("agent_ensure", agent_ensure),
        StageTiming("conversation_create", conv_create),
        StageTiming("agent_response", agent_response),
        StageTiming("activity_capture", activity_capture),
        StageTiming("conversation_delete", conv_delete),
    ]
    total_ms = sum(s.duration_ms for s in stages)

    return RunLatency(
        query=query,
        total_ms=total_ms,
        stages=stages,
        activity=activity,
        token_usage={
            "prompt_tokens": rng.randint(2500, 3600),
            "completion_tokens": rng.randint(1400, 2200),
            "total_tokens": 0,
        },
        cold_start=cold_start,
        run_index=run_index,
    )


def _run_mock(queries: list[str], repeat: int, seed: int) -> list[RunLatency]:
    rng = random.Random(seed)
    runs: list[RunLatency] = []
    for q in queries:
        for r in range(repeat):
            runs.append(_mock_run(rng, q, run_index=r, cold_start=(r == 0)))
    return runs


# ---------------------------------------------------------------------------
# Live profiling
# ---------------------------------------------------------------------------

async def _run_live(
    queries: list[str], repeat: int, warmup: bool
) -> list[RunLatency]:
    # Load .env so the orchestrator picks up endpoints / deployment names.
    try:
        from dotenv import load_dotenv

        env_path = _ROOT / ".env"
        loaded = load_dotenv(env_path, override=False)
        logger.info(
            ".env %s (%s)",
            "loaded" if loaded else "not found / empty",
            env_path,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Could not load .env (%s); relying on process env.", exc)

    # Verify the key configuration the orchestrator needs is resolvable.
    import os

    required = ["AZURE_AI_PROJECT_ENDPOINT", "AZURE_AI_MODEL_DEPLOYMENT_NAME"]
    optional = ["PIPELINE_MODE", "OUTPUT_MODE", "PERSIST_FOUNDRY_AGENTS"]
    missing = [k for k in required if not os.getenv(k)]
    present = {k: os.getenv(k) for k in optional if os.getenv(k) is not None}
    logger.info(
        "Config check | required present=%s | %s",
        not missing,
        " ".join(f"{k}={v}" for k, v in present.items()) or "(no optional flags set)",
    )
    if missing:
        logger.warning(
            "Missing required env vars: %s — live profiling will likely fail.",
            ", ".join(missing),
        )

    from agents.sequential_orchestrator_foundry import FoundryAgentOrchestrator

    orchestrator = FoundryAgentOrchestrator()
    runs: list[RunLatency] = []
    total_runs = len(queries) * repeat
    n = 0
    for q in queries:
        if warmup:
            try:
                await orchestrator.process_query_async(q)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Warmup failed for %r: %s", q, exc)
        for r in range(repeat):
            n += 1
            kind = "cold" if (r == 0 and not warmup) else "warm"
            logger.info("[%d/%d] %-4s ▶ %s", n, total_runs, kind, q[:60])
            run = await profile_orchestrator_call(
                orchestrator, q, run_index=r, cold_start=(r == 0 and not warmup)
            )
            if run.error:
                logger.warning(
                    "[%d/%d] %-4s ✗ FAILED (%s)", n, total_runs, kind, run.error[:80]
                )
            else:
                logger.info(
                    "[%d/%d] %-4s ✓ %6.0f ms (activity %.0f ms / overhead %.0f ms)",
                    n, total_runs, kind, run.total_ms,
                    run.activity_total_ms, run.overhead_ms,
                )
            runs.append(run)
    return runs


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="investigate_latency",
        description="Latency investigation for Foundry agentic retrieval.",
    )
    p.add_argument("--mock", action="store_true", help="Offline synthetic demo.")
    p.add_argument("--repeat", type=int, default=3, help="Runs per query.")
    p.add_argument("--limit", type=int, default=None, help="First N queries.")
    p.add_argument(
        "--query", action="append", default=None, help="Ad-hoc query (repeatable)."
    )
    p.add_argument("--warmup", action="store_true", help="Discard one warmup run (live).")
    p.add_argument(
        "--output", default=str(_ROOT / "logs" / "latency"), help="Report output dir."
    )
    p.add_argument("--seed", type=int, default=1234, help="RNG seed for --mock.")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)-7s | %(name)s | %(message)s",
    )
    # Silence noisy Azure SDK / OpenTelemetry HTTP logging so the latency
    # report stays the focus of the terminal output.
    for noisy in (
        "azure",
        "azure.core.pipeline.policies.http_logging_policy",
        "azure.identity",
        "opentelemetry",
        "urllib3",
        "httpx",
        "msrest",
        # App loggers — keep the terminal focused on latency progress.
        "agents",
        "search",
    ):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    # The App Insights exporter emits ERROR-level "Data drop" lines for local
    # telemetry it cannot post; these are irrelevant to the investigation.
    for silent in (
        "azure.monitor",
        "azure.monitor.opentelemetry.exporter",
        "azure.monitor.opentelemetry.exporter.export._base",
    ):
        logging.getLogger(silent).setLevel(logging.CRITICAL)
    args = _parse_args(argv)

    queries = args.query or _load_canonical_queries()
    if args.limit:
        queries = queries[: args.limit]
    if not queries:
        logger.error("No queries to profile.")
        return 2

    mode = "MOCK (offline)" if args.mock else "LIVE"
    logger.info(
        "Latency investigation | mode=%s | queries=%d | repeat=%d",
        mode, len(queries), args.repeat,
    )

    if args.mock:
        runs = _run_mock(queries, args.repeat, args.seed)
    else:
        runs = asyncio.run(_run_live(queries, args.repeat, args.warmup))

    report = aggregate_runs(
        runs,
        metadata={
            "mode": "mock" if args.mock else "live",
            "queries": len(queries),
            "repeat": args.repeat,
        },
    )

    rendered = render_markdown(report)
    # (a) Emit the report through the logger as well as stdout, so it shares
    # the stderr stream/ordering with the progress lines and never gets
    # "lost" behind block-buffered stdout when the command is piped.
    print("\n" + rendered)
    logger.info("Latency report\n%s", rendered)

    json_path, md_path = save_report(report, args.output)
    logger.info("Report written:\n  %s\n  %s", json_path, md_path)
    # (b) Also echo the saved paths on stdout (after the Markdown) so a
    # `... | tail` or stdout redirect always ends with the file locations.
    print(f"\nReport written:\n  {json_path}\n  {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
