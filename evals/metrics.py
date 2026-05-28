"""
Quality scoring metrics for the HR Policy Knowledge Lab eval harness.

Three metric families:

1. Retrieval recall@k
     Does at least one expected file appear in the top-k matches
     returned by the orchestrator/search pipeline?
     - hit@k:    1 if any expected file is in the top k, else 0
     - mrr:      1 / rank of first matched expected file (0 if none)

2. Citation accuracy
     Does the answer's MCP citation annotation(s) reference at least one
     of the expected source file names? Extracts citations using the
     pattern `【msg:search†source】` produced by the agent.

3. Answer faithfulness (LLM-as-judge)
     Given the answer + the retrieved match content, ask a judge model
     to score whether every factual claim in the answer is supported
     by the retrieved content.  Returns a 0.0–1.0 score plus rationale.
     Skipped for adversarial cases or location-only queries.

All metrics return small dataclasses so the runner can aggregate them.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Iterable

logger = logging.getLogger(__name__)

CITATION_PATTERN = re.compile(r"【(\d+):(\d+)†([^】]+)】")
REFUSAL_PATTERNS = [
    re.compile(r"\bi don'?t know\b", re.IGNORECASE),
    re.compile(r"\bnot (?:in|found in) the knowledge base\b", re.IGNORECASE),
    re.compile(r"\bcannot (?:find|locate|answer)\b", re.IGNORECASE),
    re.compile(r"\bno (?:matching|relevant) (?:policy|document|information)\b", re.IGNORECASE),
]


@dataclass
class RecallResult:
    hit_at_1: int
    hit_at_3: int
    hit_at_5: int
    mrr: float
    matched_files: list[str]
    expected_files: list[str]


@dataclass
class CitationResult:
    has_citation: bool
    citation_count: int
    expected_cited: bool
    cited_sources: list[str]


@dataclass
class FaithfulnessResult:
    score: float  # 0.0 .. 1.0
    rationale: str
    skipped: bool = False


@dataclass
class RefusalResult:
    refused: bool


def _file_basename(path: str) -> str:
    """Strip directory + URL components from a file path."""
    if not path:
        return ""
    p = path.split("?")[0]  # strip query string
    return p.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].strip()


def _normalize(name: str) -> str:
    return _file_basename(name).lower().strip()


# ---------------------------------------------------------------------------
# 1. Retrieval recall
# ---------------------------------------------------------------------------

def compute_recall(
    matches: list[dict[str, Any]],
    expected_files: Iterable[str],
) -> RecallResult:
    """Compute hit@1/3/5 and MRR against expected file names.

    A match is counted when its ``filePath`` (or ``parentTitle`` /
    ``metadata_storage_name``) basename matches any expected file
    (case-insensitive).
    """
    expected_norm = {_normalize(f) for f in expected_files if f}
    if not expected_norm:
        # Nothing expected (adversarial) — recall metrics not meaningful.
        return RecallResult(0, 0, 0, 0.0, [], [])

    matched: list[str] = []
    first_rank: int | None = None
    for rank, m in enumerate(matches, start=1):
        for key in ("filePath", "fileName", "parentTitle", "metadata_storage_name"):
            candidate = _normalize(str(m.get(key, "")))
            if candidate and candidate in expected_norm:
                matched.append(candidate)
                if first_rank is None:
                    first_rank = rank
                break

    mrr = (1.0 / first_rank) if first_rank else 0.0
    return RecallResult(
        hit_at_1=int(first_rank is not None and first_rank <= 1),
        hit_at_3=int(first_rank is not None and first_rank <= 3),
        hit_at_5=int(first_rank is not None and first_rank <= 5),
        mrr=mrr,
        matched_files=matched,
        expected_files=list(expected_norm),
    )


# ---------------------------------------------------------------------------
# 2. Citation accuracy
# ---------------------------------------------------------------------------

def compute_citation_accuracy(
    answer: str,
    expected_files: Iterable[str],
) -> CitationResult:
    """Extract MCP-style citations and verify they reference expected files."""
    expected_norm = {_normalize(f) for f in expected_files if f}
    citations = CITATION_PATTERN.findall(answer or "")
    cited_sources = [c[2].strip() for c in citations]
    cited_norm = {_normalize(s) for s in cited_sources}

    expected_cited = bool(expected_norm) and bool(expected_norm & cited_norm)
    return CitationResult(
        has_citation=bool(citations),
        citation_count=len(citations),
        expected_cited=expected_cited,
        cited_sources=cited_sources,
    )


# ---------------------------------------------------------------------------
# 3. Refusal detection (for adversarial cases)
# ---------------------------------------------------------------------------

def detect_refusal(answer: str) -> RefusalResult:
    if not answer:
        return RefusalResult(refused=False)
    for pat in REFUSAL_PATTERNS:
        if pat.search(answer):
            return RefusalResult(refused=True)
    return RefusalResult(refused=False)


# ---------------------------------------------------------------------------
# 4. Answer faithfulness — LLM-as-judge
# ---------------------------------------------------------------------------

_JUDGE_SYSTEM = """You are an evaluation judge. Your only job is to score
whether an ANSWER is faithfully grounded in the provided CONTEXT.

Rules:
- A faithful answer makes claims that are explicitly supported by the
  CONTEXT. Paraphrasing is fine; new factual claims are not.
- Ignore stylistic differences. Score only factual support.
- Output a single JSON object: {"score": <float 0..1>, "rationale": "<one sentence>"}
  - 1.0 = every claim in ANSWER is supported by CONTEXT
  - 0.5 = some claims supported, some unsupported
  - 0.0 = answer is fabricated / contradicts context
- Do not include any other text outside the JSON.
"""


def score_faithfulness(
    answer: str,
    matches: list[dict[str, Any]],
    judge_model: str | None = None,
) -> FaithfulnessResult:
    """Score answer faithfulness using an Azure OpenAI judge model.

    Requires AZURE_OPENAI_ENDPOINT and a deployment specified by
    ``EVAL_JUDGE_MODEL`` (defaults to ``gpt-4.1``). Uses managed
    identity. Returns a ``skipped`` result if the SDK or endpoint is
    unavailable.
    """
    if not answer or not matches:
        return FaithfulnessResult(0.0, "empty answer or no retrieved context", skipped=True)

    try:
        from openai import AzureOpenAI
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider
    except ImportError:  # pragma: no cover
        return FaithfulnessResult(0.0, "openai/azure-identity not installed", skipped=True)

    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "").strip()
    if not endpoint:
        return FaithfulnessResult(0.0, "AZURE_OPENAI_ENDPOINT not set", skipped=True)

    deployment = (
        judge_model
        or os.getenv("EVAL_JUDGE_MODEL")
        or os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4.1")
    )
    api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-06-01")

    token_provider = get_bearer_token_provider(
        DefaultAzureCredential(),
        "https://cognitiveservices.azure.com/.default",
    )
    client = AzureOpenAI(
        azure_endpoint=endpoint,
        api_version=api_version,
        azure_ad_token_provider=token_provider,
    )

    # Trim context to keep judge tokens bounded
    context_parts = []
    for i, m in enumerate(matches[:5]):
        content = str(m.get("content", ""))[:1500]
        source = m.get("filePath") or m.get("fileName") or f"match_{i}"
        context_parts.append(f"[{i + 1}] source={source}\n{content}")
    context_blob = "\n\n".join(context_parts)

    try:
        resp = client.chat.completions.create(
            model=deployment,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": _JUDGE_SYSTEM},
                {
                    "role": "user",
                    "content": (
                        f"CONTEXT:\n{context_blob}\n\n"
                        f"ANSWER:\n{answer}\n\n"
                        "Score the answer's faithfulness now."
                    ),
                },
            ],
        )
        raw = (resp.choices[0].message.content or "").strip()
        import json as _json

        parsed = _json.loads(raw)
        score = float(parsed.get("score", 0.0))
        rationale = str(parsed.get("rationale", ""))[:500]
        return FaithfulnessResult(score=max(0.0, min(1.0, score)), rationale=rationale)
    except Exception as e:
        logger.warning("Faithfulness judge call failed: %s", e)
        return FaithfulnessResult(0.0, f"judge error: {e}", skipped=True)
