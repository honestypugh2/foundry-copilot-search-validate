"""
Latency Investigation toolkit for Azure AI Foundry Agentic Retrieval.

This package provides an **educational, self-contained** set of modules for
investigating where time goes in the agentic-retrieval request path:

    User query
        │
        ├─ Foundry agent ensure / conversation create   (client + control plane)
        ├─ Agent response (responses.create, streaming)  (LLM reasoning + MCP tool)
        │      │
        │      └─ Knowledge Base MCP retrieve
        │             ├─ modelQueryPlanning   (decompose query → subqueries)
        │             ├─ searchIndex × N       (hybrid search per subquery)
        │             ├─ semanticReranker      (rerank candidates)
        │             └─ agenticReasoning      (synthesize extractive answer)
        │
        └─ conversation delete                          (cleanup)

Two timing sources are combined:

1. **Client wall-clock** — measured with :class:`~latency.profiler.StageTimer`
   around each orchestration stage.
2. **Service-side activity** — the ``activity[]`` array returned by Azure AI
   Search agentic retrieval, which reports ``elapsedMs`` and token counts per
   internal step. Parsed by :func:`~latency.profiler.parse_activity`.

The toolkit then aggregates many runs into percentile statistics
(:mod:`latency.analysis`) and renders Markdown / JSON reports
(:mod:`latency.report`) so you can identify bottlenecks, cold-start penalties,
and high-variance stages.

Public API:
    StageTimer, parse_activity, summarize_activity, profile_orchestrator_call
    RunLatency, StageTiming, ActivityStage, LatencyStats, LatencyReport
    compute_stats, percentile, aggregate_runs, identify_bottlenecks
    render_markdown, report_to_dict, save_report
"""

from latency.models import (
    ActivityStage,
    Finding,
    LatencyReport,
    LatencyStats,
    RunLatency,
    StageTiming,
    STAGE_ACTIVITY_CAPTURE,
    STAGE_AGENT_DELETE,
    STAGE_AGENT_ENSURE,
    STAGE_AGENT_RESPONSE,
    STAGE_CLIENT_SETUP,
    STAGE_CONVERSATION_CREATE,
    STAGE_CONVERSATION_DELETE,
    STAGE_TOTAL,
)
from latency.profiler import (
    StageTimer,
    parse_activity,
    profile_orchestrator_call,
    summarize_activity,
)
from latency.analysis import (
    aggregate_runs,
    analyze_findings,
    compute_stats,
    identify_bottlenecks,
    percentile,
)
from latency.report import (
    render_markdown,
    report_to_dict,
    save_report,
)

__all__ = [
    # models
    "ActivityStage",
    "Finding",
    "LatencyReport",
    "LatencyStats",
    "RunLatency",
    "StageTiming",
    "STAGE_ACTIVITY_CAPTURE",
    "STAGE_AGENT_DELETE",
    "STAGE_AGENT_ENSURE",
    "STAGE_AGENT_RESPONSE",
    "STAGE_CLIENT_SETUP",
    "STAGE_CONVERSATION_CREATE",
    "STAGE_CONVERSATION_DELETE",
    "STAGE_TOTAL",
    # profiler
    "StageTimer",
    "parse_activity",
    "profile_orchestrator_call",
    "summarize_activity",
    # analysis
    "aggregate_runs",
    "analyze_findings",
    "compute_stats",
    "identify_bottlenecks",
    "percentile",
    # report
    "render_markdown",
    "report_to_dict",
    "save_report",
]
