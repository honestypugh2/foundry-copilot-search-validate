"""
Aggregation and bottleneck analysis for latency runs.

* :func:`percentile`        – linear-interpolated percentile of a sample.
* :func:`compute_stats`     – mean/p50/p90/p95/p99/min/max/stdev for a label.
* :func:`aggregate_runs`    – roll many :class:`RunLatency` into a report.
* :func:`identify_bottlenecks` – heuristic, human-readable findings.
"""

from __future__ import annotations

import statistics
from typing import Iterable

from latency.models import (
    Finding,
    LatencyReport,
    LatencyStats,
    RunLatency,
)

# Heuristic thresholds for bottleneck findings (tunable, documented inline).
_DOMINANT_SHARE = 0.40       # a stage taking ≥40% of total is "dominant"
_HIGH_VARIANCE_CV = 0.50     # coeff. of variation ≥0.5 ⇒ unstable latency
_COLD_PENALTY_RATIO = 1.30   # cold p50 ≥1.3× warm p50 ⇒ notable cold start

# ---------------------------------------------------------------------------
# Definitions + mitigations for each finding category.
# These make the report self-documenting: every finding carries an
# explanation of *what it means* and *how to avoid it*.
# ---------------------------------------------------------------------------
_OVERHEAD_DEF = (
    "End-to-end time is dominated by work outside the Azure AI Search "
    "knowledge-base activity trace: client SDK calls, Foundry agent and "
    "conversation control-plane operations (ensure agent, create/delete "
    "conversation), network round-trips, and the LLM answer-synthesis "
    "streaming that the agentic-retrieval activity[] does not measure."
)
_OVERHEAD_FIXES = [
    "Persist and reuse agents (PERSIST_FOUNDRY_AGENTS=true) so each request "
    "skips agent create/delete.",
    "Reuse one orchestrator / HTTP client across requests for connection "
    "pooling instead of rebuilding clients per call.",
    "Add a warmup request so first-call auth/connection setup is excluded "
    "from user-facing latency.",
    "Use outputMode=extractiveData when a synthesized answer is not required "
    "— this skips the LLM synthesis pass that inflates response streaming.",
    "Co-locate the app, Foundry project, and Search service in one region to "
    "cut network round-trip time.",
]

_ACTIVITY_GUIDE: dict[str, tuple[str, list[str]]] = {
    "modelQueryPlanning": (
        "The LLM query-planning step — decomposing the question into search "
        "sub-queries — is the largest contributor. This is reasoning-model "
        "time spent before any index search runs.",
        [
            "Lower retrievalReasoningEffort (medium → low → minimal) to shorten "
            "planning.",
            "Use a faster/smaller planner model deployment for decomposition.",
            "Cache or template plans for recurring query shapes.",
        ],
    ),
    "searchIndex": (
        "Executing the planned search sub-queries against the Azure AI Search "
        "index dominates the measured activity time.",
        [
            "Reduce sub-query fan-out (lower reasoningEffort) so fewer index "
            "round-trips run.",
            "Tune the index: right-size vector dimensions, enable compression, "
            "and provision adequate replicas/partitions for the query volume.",
            "Scale up the Search tier or add replicas to raise query throughput.",
        ],
    ),
    "semanticReranker": (
        "The semantic reranking pass (L2 re-scoring of candidate results) "
        "dominates the measured activity time.",
        [
            "Lower the reranked candidate count (top / maxOutputSize) so fewer "
            "documents are scored.",
            "Reduce sub-query fan-out so the reranker receives fewer inputs.",
        ],
    ),
    "agenticReasoning": (
        "The agentic reasoning / answer-synthesis step dominates the measured "
        "activity time (token generation, not index work).",
        [
            "Switch outputMode to extractiveData to skip answer synthesis.",
            "Lower retrievalReasoningEffort to reduce reasoning tokens.",
        ],
    ),
}

_VARIANCE_DEF = (
    "Latency is unstable: the gap between typical (p50) and tail (p99) "
    "requests is wide (coefficient of variation ≥ 0.5). Users experience "
    "inconsistent response times, usually from cold starts, throttling, or "
    "noisy-neighbour effects on shared capacity."
)
_VARIANCE_FIXES = [
    "Keep instances warm (PERSIST_FOUNDRY_AGENTS, warmup pings, min-instances) "
    "to cut cold-start tails.",
    "Check Search/OpenAI for 429 throttling; raise quota and add retry with "
    "exponential backoff.",
    "Pin capacity (provisioned throughput / dedicated tiers) to reduce "
    "shared-tenant variability.",
]

_COLD_DEF = (
    "The first request to an idle agent/host pays a one-time penalty — agent "
    "creation, auth-token acquisition, TLS/connection setup, and model load. "
    "Here the cold p50 is at least 1.3× the warm p50."
)
_COLD_FIXES = [
    "Enable PERSIST_FOUNDRY_AGENTS so agents are reused rather than recreated.",
    "Issue a startup warmup request (plus periodic keep-alive) so users never "
    "hit the cold path.",
    "Use always-on hosting / minimum instance counts to avoid scale-to-zero.",
]

_PLANNING_DEF = (
    "The LLM query-planning step takes longer than the index search it "
    "produces — you spend more time deciding what to search for than actually "
    "searching. This signals reasoning effort disproportionate to retrieval "
    "cost."
)
_PLANNING_FIXES = [
    "Lower retrievalReasoningEffort (medium → low → minimal).",
    "Use a faster planner model deployment.",
    "For simple lookups, bypass agentic planning and call direct/keyword "
    "search.",
]

_EVEN_DEF = (
    "No single stage exceeds 40% of total latency — time is spread across "
    "planning, search, reranking, and overhead. There is no obvious hotspot, "
    "so gains require broad tuning rather than one targeted fix."
)
_EVEN_FIXES = [
    "Pursue incremental wins across stages (reasoning effort, warm agents, "
    "region co-location).",
    "Set a per-stage latency budget and track regressions over time.",
]

# Client-side stage attribution. When the overhead bucket dominates AND the
# orchestrator reported explicit stage timings, we point at the *specific*
# stage responsible so the mitigation is accurate (e.g. don't suggest
# "persist agents" when agent lifecycle is already cheap and the real cost is
# the model's reasoning/synthesis pass).
_STAGE_GUIDE: dict[str, tuple[str, list[str]]] = {
    "client_setup": (
        "Acquiring the Azure credential / OAuth token and constructing the "
        "project + OpenAI clients dominates. This is per-request connection "
        "and auth setup, not model work.",
        [
            "Reuse one credential and one set of clients across requests "
            "(construct once, not per call) so token + TLS setup is amortised.",
            "Rely on the credential's token cache; warm it at startup with a "
            "throwaway request.",
            "Keep the host warm (min-instances) so setup isn't repaid on each "
            "cold invocation.",
        ],
    ),
    "agent_ensure": (
        "Getting-or-creating the Foundry agent and resolving its version "
        "dominates. This is agent control-plane work before any inference.",
        [
            "Ensure PERSIST_FOUNDRY_AGENTS=true so the agent is reused rather "
            "than recreated.",
            "If persistence is already enabled, cache the resolved agent "
            "handle in-process so each request skips the get/version lookup.",
            "Avoid RECREATE_FOUNDRY_AGENTS in steady state — only recreate "
            "after editing instructions/tools.",
        ],
    ),
    "conversation_create": (
        "Creating the conversation/thread dominates — control-plane latency "
        "for each new session.",
        [
            "Reuse a conversation across turns instead of creating one per "
            "request.",
            "Co-locate the app with the Foundry project to cut round-trip time.",
        ],
    ),
    "agent_response": (
        "The model's reasoning + answer-synthesis pass dominates — the agent "
        "plans sub-queries, calls the knowledge-base MCP tool, and streams the "
        "answer. For reasoning models in ANSWER_SYNTHESIS mode this is the "
        "expected hotspot, and it is NOT fixed by persisting agents.",
        [
            "Lower retrievalReasoningEffort (medium → low → minimal) to cut "
            "planning/reasoning time.",
            "Use outputMode=extractiveData when a synthesized prose answer is "
            "not required — this skips the LLM synthesis pass entirely.",
            "Use a faster / smaller model deployment (e.g. a non-reasoning or "
            "mini model) for latency-sensitive paths.",
            "Reduce MCP tool round-trips (fewer sub-queries) and rely on "
            "streaming so time-to-first-token stays low.",
        ],
    ),
    "activity_capture": (
        "A second, direct agentic_retrieve call — made only to expose the "
        "service-side activity[] breakdown — dominates. This duplicates the "
        "agent's own retrieval purely for observability.",
        [
            "Disable or sample the extra activity-capture retrieval in "
            "production; it doubles retrieval work.",
            "Capture activity from the agent's own tool output instead of "
            "issuing a separate retrieve call.",
        ],
    ),
    "conversation_delete": (
        "Tearing down the conversation dominates — control-plane cleanup.",
        [
            "Reuse conversations instead of create/delete per request.",
            "Delete asynchronously / in the background off the request path.",
        ],
    ),
    "agent_delete": (
        "Deleting the agent version dominates — happens only when "
        "PERSIST_FOUNDRY_AGENTS is false.",
        [
            "Set PERSIST_FOUNDRY_AGENTS=true so agents are reused and never "
            "deleted on the request path.",
        ],
    ),
}




def percentile(values: Iterable[float], pct: float) -> float:
    """Return the ``pct`` percentile (0–100) using linear interpolation.

    Matches the common "linear"/numpy default method. Empty input → 0.0.
    """
    data = sorted(float(v) for v in values)
    if not data:
        return 0.0
    if len(data) == 1:
        return data[0]
    pct = max(0.0, min(100.0, pct))
    rank = (pct / 100.0) * (len(data) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(data) - 1)
    frac = rank - lo
    return data[lo] + (data[hi] - data[lo]) * frac


def compute_stats(label: str, values: Iterable[float]) -> LatencyStats:
    """Compute a :class:`LatencyStats` summary for ``values`` (ms)."""
    data = [float(v) for v in values]
    if not data:
        return LatencyStats(label, 0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
    return LatencyStats(
        label=label,
        count=len(data),
        mean_ms=statistics.fmean(data),
        p50_ms=percentile(data, 50),
        p90_ms=percentile(data, 90),
        p95_ms=percentile(data, 95),
        p99_ms=percentile(data, 99),
        min_ms=min(data),
        max_ms=max(data),
        stdev_ms=statistics.pstdev(data) if len(data) > 1 else 0.0,
    )


def aggregate_runs(
    runs: list[RunLatency],
    *,
    metadata: dict | None = None,
) -> LatencyReport:
    """Roll a list of runs into a :class:`LatencyReport`.

    Only successful runs (``error is None``) contribute to timing statistics;
    failed runs are counted in ``error_count`` and retained in ``runs``.
    """
    ok = [r for r in runs if r.error is None]
    errors = [r for r in runs if r.error is not None]

    total = compute_stats("total", [r.total_ms for r in ok])
    overhead = compute_stats("overhead", [r.overhead_ms for r in ok])
    activity_total = compute_stats("activity_total", [r.activity_total_ms for r in ok])

    # Per client-side stage (excluding the synthetic "total" stage).
    stage_names: list[str] = []
    for r in ok:
        for s in r.stages:
            if s.name != "total" and s.name not in stage_names:
                stage_names.append(s.name)
    by_stage: dict[str, LatencyStats] = {
        name: compute_stats(name, [r.stage_ms(name) for r in ok if r.stage_ms(name) > 0])
        for name in stage_names
    }

    # Per service-side activity type.
    activity_types: list[str] = []
    for r in ok:
        for t in r.activity_ms_by_type():
            if t not in activity_types:
                activity_types.append(t)
    by_activity_type: dict[str, LatencyStats] = {
        t: compute_stats(t, [r.activity_ms_by_type().get(t, 0.0) for r in ok])
        for t in activity_types
    }

    # Cold vs. warm split.
    cold_vals = [r.total_ms for r in ok if r.cold_start]
    warm_vals = [r.total_ms for r in ok if not r.cold_start]
    cold = compute_stats("cold", cold_vals) if cold_vals else None
    warm = compute_stats("warm", warm_vals) if warm_vals else None

    report = LatencyReport(
        run_count=len(runs),
        error_count=len(errors),
        total=total,
        overhead=overhead,
        activity_total=activity_total,
        by_stage=by_stage,
        by_activity_type=by_activity_type,
        cold=cold,
        warm=warm,
        runs=runs,
        metadata=dict(metadata or {}),
    )
    report.findings = analyze_findings(report)
    report.bottlenecks = [f.message for f in report.findings]
    return report


def _dominant_stage_finding(report: LatencyReport) -> Finding | None:
    """Identify the dominant client-side stage and return a tailored finding.

    Only fires when the orchestrator reported explicit ``by_stage`` timings.
    Attributes the overhead to a specific boundary (e.g. ``agent_response``)
    so the mitigation is accurate rather than generic.
    """
    if not report.by_stage:
        return None
    name, stats = max(report.by_stage.items(), key=lambda kv: kv[1].p50_ms)
    if stats.p50_ms <= 0:
        return None
    guide = _STAGE_GUIDE.get(name)
    if guide is None:
        return None
    definition, fixes = guide
    total_p50 = report.total.p50_ms or 0.0
    share = (stats.p50_ms / total_p50) if total_p50 else 0.0
    message = (
        f"Dominant client stage is '{name}': {stats.p50_ms:.0f} ms p50"
        + (f" ({share * 100:.0f}% of total)." if total_p50 else ".")
    )
    return Finding(f"stage_{name}", message, definition, list(fixes))


def analyze_findings(report: LatencyReport) -> list[Finding]:
    """Produce structured findings (message + definition + mitigations)."""
    findings: list[Finding] = []
    total_p50 = report.total.p50_ms
    if total_p50 <= 0:
        return findings

    # 1. Dominant contributors (service-side type or overhead).
    contributors: dict[str, float] = {
        t: s.p50_ms for t, s in report.by_activity_type.items()
    }
    contributors["client/agent overhead"] = report.overhead.p50_ms
    for name, p50 in sorted(contributors.items(), key=lambda kv: kv[1], reverse=True):
        share = p50 / total_p50 if total_p50 else 0.0
        if share < _DOMINANT_SHARE:
            continue
        message = (
            f"'{name}' dominates latency: {p50:.0f} ms p50 "
            f"({share * 100:.0f}% of total {total_p50:.0f} ms)."
        )
        if name == "client/agent overhead":
            findings.append(
                Finding("overhead_dominates", message, _OVERHEAD_DEF, list(_OVERHEAD_FIXES))
            )
            # If the orchestrator reported explicit client-side stages, point
            # at the specific stage responsible so the mitigation is accurate.
            stage_finding = _dominant_stage_finding(report)
            if stage_finding is not None:
                findings.append(stage_finding)
        elif name in _ACTIVITY_GUIDE:
            definition, fixes = _ACTIVITY_GUIDE[name]
            findings.append(
                Finding(f"{name}_dominates", message, definition, list(fixes))
            )
        else:
            findings.append(
                Finding(
                    "activity_dominates",
                    message,
                    f"The '{name}' service-side step accounts for most of the "
                    "measured activity time.",
                    [
                        "Reduce sub-query fan-out via a lower retrievalReasoningEffort.",
                        "Scale or tune the underlying Search service.",
                    ],
                )
            )

    # 2. High variance (unstable) total latency.
    if report.total.mean_ms > 0:
        cv = report.total.stdev_ms / report.total.mean_ms
        if cv >= _HIGH_VARIANCE_CV:
            findings.append(
                Finding(
                    "high_variance",
                    f"High latency variance (CV={cv:.2f}): p50={report.total.p50_ms:.0f} ms "
                    f"vs p99={report.total.p99_ms:.0f} ms — investigate tail/cold starts.",
                    _VARIANCE_DEF,
                    list(_VARIANCE_FIXES),
                )
            )

    # 3. Cold-start penalty.
    if report.cold and report.warm and report.warm.p50_ms > 0:
        ratio = report.cold.p50_ms / report.warm.p50_ms
        if ratio >= _COLD_PENALTY_RATIO:
            findings.append(
                Finding(
                    "cold_start",
                    f"Cold-start penalty: cold p50={report.cold.p50_ms:.0f} ms is "
                    f"{ratio:.1f}× warm p50={report.warm.p50_ms:.0f} ms — "
                    "consider PERSIST_FOUNDRY_AGENTS / warmup.",
                    _COLD_DEF,
                    list(_COLD_FIXES),
                )
            )

    # 4. Query planning heavier than search.
    plan = report.by_activity_type.get("modelQueryPlanning")
    search = report.by_activity_type.get("searchIndex")
    if plan and search and search.p50_ms > 0 and plan.p50_ms > search.p50_ms:
        findings.append(
            Finding(
                "planning_vs_search",
                f"Query planning ({plan.p50_ms:.0f} ms p50) exceeds index search "
                f"({search.p50_ms:.0f} ms p50) — lower retrieval_reasoning_effort or "
                "use a faster planner model.",
                _PLANNING_DEF,
                list(_PLANNING_FIXES),
            )
        )

    if not findings:
        findings.append(
            Finding(
                "even_spread",
                f"No single dominant bottleneck; latency is spread evenly "
                f"(total p50={total_p50:.0f} ms).",
                _EVEN_DEF,
                list(_EVEN_FIXES),
            )
        )
    return findings


def identify_bottlenecks(report: LatencyReport) -> list[str]:
    """Human-readable finding messages (compat shim over :func:`analyze_findings`)."""
    return [f.message for f in analyze_findings(report)]

