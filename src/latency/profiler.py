"""
Profiling primitives for the latency investigation.

* :class:`StageTimer` – a lightweight, dependency-free context-manager based
  wall-clock timer for client-side stages.
* :func:`parse_activity` – normalise the agentic-retrieval ``activity[]`` array
  (camelCase or snake_case) into :class:`~latency.models.ActivityStage`.
* :func:`summarize_activity` – group activity steps by type.
* :func:`profile_orchestrator_call` – run a query through an orchestrator while
  capturing total wall-clock time, the service-side activity breakdown, and
  token usage, returning a :class:`~latency.models.RunLatency`.

Design note
-----------
``profile_orchestrator_call`` is intentionally **non-invasive**: it times the
public ``process_query_async`` boundary and derives the service/overhead split
from the returned ``activity[]``. This avoids forking the orchestrator's
internal control flow while still giving an actionable breakdown
(query planning vs. search vs. reasoning vs. client/agent overhead).
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from typing import Any, Iterator

from latency.models import (
    ActivityStage,
    RunLatency,
    StageTiming,
    STAGE_TOTAL,
)

logger = logging.getLogger("latency.profiler")


class StageTimer:
    """Accumulate named wall-clock stage timings using ``perf_counter``.

    Example::

        timer = StageTimer()
        with timer.measure("agent_ensure"):
            ...
        with timer.measure("agent_response", model="gpt-4.1-mini"):
            ...
        print(timer.total_ms, timer.stages)
    """

    def __init__(self) -> None:
        self._stages: list[StageTiming] = []

    @contextmanager
    def measure(self, name: str, **metadata: Any) -> Iterator[None]:
        """Time the wrapped block and record it as a stage."""
        start = time.perf_counter()
        try:
            yield
        finally:
            duration_ms = (time.perf_counter() - start) * 1000.0
            self._stages.append(StageTiming(name, duration_ms, dict(metadata)))

    @property
    def stages(self) -> list[StageTiming]:
        return list(self._stages)

    @property
    def total_ms(self) -> float:
        return sum(s.duration_ms for s in self._stages)


# ---------------------------------------------------------------------------
# Activity parsing
# ---------------------------------------------------------------------------

def _first(d: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Return the first present key from ``keys`` (camelCase/snake_case)."""
    for k in keys:
        if k in d and d[k] is not None:
            return d[k]
    return default


def parse_activity(activity: list[dict[str, Any]] | None) -> list[ActivityStage]:
    """Normalise a raw ``activity[]`` array into :class:`ActivityStage` items.

    Handles both the live REST camelCase contract (``elapsedMs``,
    ``inputTokens``, ``outputTokens``, ``reasoningTokens``) and the
    snake_case form used in some docs (``elapsed_ms``, ``input_tokens``).
    Steps with no ``elapsedMs`` (e.g. ``agenticReasoning``) record 0.0.
    """
    parsed: list[ActivityStage] = []
    for raw in activity or []:
        if not isinstance(raw, dict):
            continue
        parsed.append(
            ActivityStage(
                step_id=int(_first(raw, "id", "step_id", default=len(parsed))),
                type=str(_first(raw, "type", default="unknown")),
                elapsed_ms=float(_first(raw, "elapsedMs", "elapsed_ms", default=0.0)),
                input_tokens=int(_first(raw, "inputTokens", "input_tokens", default=0)),
                output_tokens=int(_first(raw, "outputTokens", "output_tokens", default=0)),
                reasoning_tokens=int(
                    _first(raw, "reasoningTokens", "reasoning_tokens", default=0)
                ),
                result_count=int(_first(raw, "count", default=0)),
                detail={
                    k: v
                    for k, v in raw.items()
                    if k
                    not in {
                        "id",
                        "type",
                        "elapsedMs",
                        "elapsed_ms",
                        "inputTokens",
                        "input_tokens",
                        "outputTokens",
                        "output_tokens",
                        "reasoningTokens",
                        "reasoning_tokens",
                        "count",
                    }
                },
            )
        )
    return parsed


def summarize_activity(stages: list[ActivityStage]) -> dict[str, dict[str, float]]:
    """Group activity steps by type with elapsed/token/count totals.

    Returns ``{type: {"elapsed_ms", "count", "input_tokens",
    "output_tokens", "reasoning_tokens", "steps"}}``.
    """
    summary: dict[str, dict[str, float]] = {}
    for s in stages:
        bucket = summary.setdefault(
            s.type,
            {
                "elapsed_ms": 0.0,
                "count": 0.0,
                "input_tokens": 0.0,
                "output_tokens": 0.0,
                "reasoning_tokens": 0.0,
                "steps": 0.0,
            },
        )
        bucket["elapsed_ms"] += s.elapsed_ms
        bucket["count"] += s.result_count
        bucket["input_tokens"] += s.input_tokens
        bucket["output_tokens"] += s.output_tokens
        bucket["reasoning_tokens"] += s.reasoning_tokens
        bucket["steps"] += 1
    return summary


# ---------------------------------------------------------------------------
# Orchestrator profiling
# ---------------------------------------------------------------------------

async def profile_orchestrator_call(
    orchestrator: Any,
    query: str,
    *,
    run_index: int = 0,
    cold_start: bool = False,
) -> RunLatency:
    """Run ``query`` through ``orchestrator`` and capture a RunLatency.

    The orchestrator must expose an awaitable
    ``process_query_async(query) -> dict`` returning at least ``activity``
    and (optionally) ``token_usage``. The total wall-clock duration is
    measured around that call; the service/overhead split is derived from
    the returned ``activity[]``.
    """
    timer = StageTimer()
    out: dict[str, Any] = {}
    error: str | None = None

    with timer.measure(STAGE_TOTAL, query=query):
        try:
            out = await orchestrator.process_query_async(query)
        except Exception as exc:  # noqa: BLE001 — capture for the report
            error = f"{type(exc).__name__}: {exc}"
            logger.warning("Profiled query failed: %s", error)

    activity = parse_activity(out.get("activity") if isinstance(out, dict) else None)
    token_usage = (
        dict(out.get("token_usage") or {}) if isinstance(out, dict) else {}
    )

    # Merge any client-side stage timings the orchestrator recorded (via its
    # own StageTimer) so the report can attribute overhead to explicit
    # boundaries (client setup / agent ensure / agent response / ...).
    stages = list(timer.stages)
    raw_stages = out.get("stage_timings") if isinstance(out, dict) else None
    for st in raw_stages or []:
        if not isinstance(st, dict):
            continue
        stages.append(
            StageTiming(
                name=str(st.get("name", "unknown")),
                duration_ms=float(st.get("duration_ms", 0.0)),
                metadata=dict(st.get("metadata") or {}),
            )
        )

    return RunLatency(
        query=query,
        total_ms=timer.total_ms,
        stages=stages,
        activity=activity,
        token_usage=token_usage,
        cold_start=cold_start,
        run_index=run_index,
        error=error,
    )
