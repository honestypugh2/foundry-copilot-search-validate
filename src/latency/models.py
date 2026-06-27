"""
Dataclasses for the latency-investigation toolkit.

These model three layers of timing data:

* :class:`StageTiming`   – one client-side wall-clock stage (perf_counter).
* :class:`ActivityStage` – one service-side step parsed from the agentic
  retrieval ``activity[]`` array (``elapsedMs`` + token counts).
* :class:`RunLatency`    – everything captured for a single query run.

and two layers of aggregation:

* :class:`LatencyStats`  – percentile summary for a labelled set of durations.
* :class:`LatencyReport` – the full rolled-up report across many runs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Canonical client-side stage names (single-agent MCP pipeline)
# ---------------------------------------------------------------------------
# These mirror the boundaries inside
# ``agents.sequential_orchestrator_foundry.FoundryAgentOrchestrator
#   ._single_agent_pipeline``.
STAGE_CLIENT_SETUP = "client_setup"                # credential + project/openai client
STAGE_AGENT_ENSURE = "agent_ensure"               # get-or-create Foundry agent
STAGE_CONVERSATION_CREATE = "conversation_create"  # openai conversations.create
STAGE_AGENT_RESPONSE = "agent_response"            # responses.create (stream)
STAGE_ACTIVITY_CAPTURE = "activity_capture"        # direct agentic_retrieve
STAGE_CONVERSATION_DELETE = "conversation_delete"  # cleanup
STAGE_AGENT_DELETE = "agent_delete"                # delete agent version (non-persist)
STAGE_TOTAL = "total"                              # whole process_query_async

# Service-side activity step types emitted by Azure AI Search agentic retrieval.
ACTIVITY_QUERY_PLANNING = "modelQueryPlanning"
ACTIVITY_SEARCH_INDEX = "searchIndex"
ACTIVITY_SEMANTIC_RERANKER = "semanticReranker"
ACTIVITY_AGENTIC_REASONING = "agenticReasoning"


@dataclass
class StageTiming:
    """A single client-side wall-clock measurement."""

    name: str
    duration_ms: float
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ActivityStage:
    """One service-side step from the agentic retrieval ``activity[]`` array.

    The live REST contract uses camelCase (``elapsedMs``, ``inputTokens``);
    the documentation/walkthrough sometimes uses snake_case. The parser in
    :func:`latency.profiler.parse_activity` normalises both into this shape.
    """

    step_id: int
    type: str
    elapsed_ms: float = 0.0
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    result_count: int = 0
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class RunLatency:
    """All timing data captured for one query run."""

    query: str
    total_ms: float
    stages: list[StageTiming] = field(default_factory=list)
    activity: list[ActivityStage] = field(default_factory=list)
    token_usage: dict[str, int] = field(default_factory=dict)
    cold_start: bool = False
    run_index: int = 0
    error: str | None = None

    # ----------------------------------------------------------------- helpers
    @property
    def activity_total_ms(self) -> float:
        """Sum of service-side ``elapsedMs`` across all activity steps."""
        return sum(a.elapsed_ms for a in self.activity)

    @property
    def overhead_ms(self) -> float:
        """Time not attributable to service-side activity.

        ``total - activity_total``. This captures client SDK overhead,
        control-plane calls (agent ensure, conversation create/delete),
        network round-trips, and LLM streaming not reflected in the
        knowledge-base activity trace. Never negative.
        """
        return max(0.0, self.total_ms - self.activity_total_ms)

    def stage_ms(self, name: str) -> float:
        """Total duration for a named client-side stage (0.0 if absent)."""
        return sum(s.duration_ms for s in self.stages if s.name == name)

    def activity_ms_by_type(self) -> dict[str, float]:
        """Aggregate service-side ``elapsedMs`` grouped by step type."""
        out: dict[str, float] = {}
        for a in self.activity:
            out[a.type] = out.get(a.type, 0.0) + a.elapsed_ms
        return out


@dataclass
class LatencyStats:
    """Percentile summary for a labelled collection of durations (ms)."""

    label: str
    count: int
    mean_ms: float
    p50_ms: float
    p90_ms: float
    p95_ms: float
    p99_ms: float
    min_ms: float
    max_ms: float
    stdev_ms: float


@dataclass
class Finding:
    """A single bottleneck finding with its definition and mitigations.

    ``message`` is the data-specific observation (e.g. percentages and ms),
    ``definition`` explains *what the finding means*, and ``mitigations`` are
    concrete actions to reduce or avoid it.
    """

    code: str
    message: str
    definition: str
    mitigations: list[str] = field(default_factory=list)


@dataclass
class LatencyReport:
    """The full rolled-up latency investigation report."""

    run_count: int
    error_count: int
    total: LatencyStats
    overhead: LatencyStats
    activity_total: LatencyStats
    by_stage: dict[str, LatencyStats] = field(default_factory=dict)
    by_activity_type: dict[str, LatencyStats] = field(default_factory=dict)
    cold: LatencyStats | None = None
    warm: LatencyStats | None = None
    bottlenecks: list[str] = field(default_factory=list)
    findings: list[Finding] = field(default_factory=list)
    runs: list[RunLatency] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
