"""
Offline unit tests for the latency-investigation toolkit (src/latency).

These tests are fully deterministic and require no Azure connectivity, so
they are marked ``mock`` and run in CI without credentials::

    PYTHONPATH=$PWD/src pytest tests/test_latency_investigation.py -v

Coverage:
    * StageTimer wall-clock capture
    * parse_activity camelCase + snake_case normalisation
    * summarize_activity grouping
    * percentile / compute_stats correctness
    * RunLatency derived properties (overhead / activity split)
    * aggregate_runs roll-up (cold/warm, per-type, per-stage)
    * identify_bottlenecks heuristics
    * report rendering + persistence
    * profile_orchestrator_call against a fake orchestrator
"""

from __future__ import annotations

import json
import time

import pytest

from latency import (
    RunLatency,
    StageTimer,
    aggregate_runs,
    compute_stats,
    identify_bottlenecks,
    parse_activity,
    percentile,
    profile_orchestrator_call,
    render_markdown,
    report_to_dict,
    save_report,
    summarize_activity,
)
from latency.models import StageTiming

pytestmark = pytest.mark.mock


# ---------------------------------------------------------------------------
# Representative live-shaped activity payload (camelCase, from captured JSON)
# ---------------------------------------------------------------------------
_LIVE_ACTIVITY = [
    {
        "type": "modelQueryPlanning",
        "id": 0,
        "elapsedMs": 3520,
        "inputTokens": 1687,
        "outputTokens": 193,
    },
    {
        "type": "searchIndex",
        "id": 1,
        "knowledgeSourceName": "hr-knowledge-source",
        "elapsedMs": 118,
        "count": 6,
    },
    {
        "type": "searchIndex",
        "id": 2,
        "elapsedMs": 78,
        "count": 7,
    },
    {
        "type": "agenticReasoning",
        "id": 3,
        "reasoningTokens": 11962,
    },
]


# ---------------------------------------------------------------------------
# StageTimer
# ---------------------------------------------------------------------------

def test_stage_timer_records_named_stages():
    timer = StageTimer()
    with timer.measure("agent_ensure", model="gpt-4.1-mini"):
        time.sleep(0.005)
    with timer.measure("agent_response"):
        time.sleep(0.005)

    names = [s.name for s in timer.stages]
    assert names == ["agent_ensure", "agent_response"]
    assert all(s.duration_ms > 0 for s in timer.stages)
    assert timer.stages[0].metadata == {"model": "gpt-4.1-mini"}
    assert timer.total_ms == pytest.approx(sum(s.duration_ms for s in timer.stages))


def test_stage_timer_records_stage_even_on_exception():
    timer = StageTimer()
    with pytest.raises(ValueError):
        with timer.measure("boom"):
            raise ValueError("x")
    assert [s.name for s in timer.stages] == ["boom"]


# ---------------------------------------------------------------------------
# parse_activity / summarize_activity
# ---------------------------------------------------------------------------

def test_parse_activity_camelcase():
    stages = parse_activity(_LIVE_ACTIVITY)
    assert len(stages) == 4
    planning = stages[0]
    assert planning.type == "modelQueryPlanning"
    assert planning.elapsed_ms == 3520
    assert planning.input_tokens == 1687
    assert planning.output_tokens == 193
    # agenticReasoning has no elapsedMs → defaults to 0, keeps reasoning tokens
    reasoning = stages[3]
    assert reasoning.elapsed_ms == 0.0
    assert reasoning.reasoning_tokens == 11962


def test_parse_activity_snake_case():
    stages = parse_activity(
        [{"type": "searchIndex", "id": 5, "elapsed_ms": 99, "count": 3}]
    )
    assert stages[0].elapsed_ms == 99
    assert stages[0].result_count == 3
    assert stages[0].step_id == 5


def test_parse_activity_handles_none_and_junk():
    assert parse_activity(None) == []
    assert parse_activity([None, "x", 3]) == []  # type: ignore[list-item]


def test_summarize_activity_groups_by_type():
    summary = summarize_activity(parse_activity(_LIVE_ACTIVITY))
    assert summary["searchIndex"]["elapsed_ms"] == 196  # 118 + 78
    assert summary["searchIndex"]["steps"] == 2
    assert summary["searchIndex"]["count"] == 13
    assert summary["modelQueryPlanning"]["input_tokens"] == 1687


# ---------------------------------------------------------------------------
# percentile / compute_stats
# ---------------------------------------------------------------------------

def test_percentile_linear_interpolation():
    data = [10, 20, 30, 40]
    assert percentile(data, 0) == 10
    assert percentile(data, 100) == 40
    assert percentile(data, 50) == 25  # midpoint of 20 and 30


def test_percentile_edge_cases():
    assert percentile([], 50) == 0.0
    assert percentile([42], 99) == 42


def test_compute_stats_basic():
    stats = compute_stats("total", [100, 200, 300])
    assert stats.count == 3
    assert stats.mean_ms == pytest.approx(200)
    assert stats.min_ms == 100
    assert stats.max_ms == 300
    assert stats.p50_ms == pytest.approx(200)


# ---------------------------------------------------------------------------
# RunLatency derived properties
# ---------------------------------------------------------------------------

def _make_run(total_ms: float, activity, cold=False, run_index=0) -> RunLatency:
    return RunLatency(
        query="q",
        total_ms=total_ms,
        stages=[
            StageTiming("agent_ensure", total_ms * 0.1),
            StageTiming("agent_response", total_ms * 0.8),
        ],
        activity=parse_activity(activity),
        cold_start=cold,
        run_index=run_index,
    )


def test_run_overhead_split():
    run = _make_run(20000, _LIVE_ACTIVITY)
    assert run.activity_total_ms == 3520 + 118 + 78  # reasoning has 0 elapsed
    assert run.overhead_ms == pytest.approx(20000 - run.activity_total_ms)


def test_run_overhead_never_negative():
    run = _make_run(100, _LIVE_ACTIVITY)  # activity_total > total
    assert run.overhead_ms == 0.0


def test_run_activity_ms_by_type():
    run = _make_run(20000, _LIVE_ACTIVITY)
    by_type = run.activity_ms_by_type()
    assert by_type["searchIndex"] == 196
    assert by_type["modelQueryPlanning"] == 3520


# ---------------------------------------------------------------------------
# aggregate_runs
# ---------------------------------------------------------------------------

def test_aggregate_runs_cold_warm_and_errors():
    runs = [
        _make_run(30000, _LIVE_ACTIVITY, cold=True, run_index=0),
        _make_run(15000, _LIVE_ACTIVITY, cold=False, run_index=1),
        _make_run(16000, _LIVE_ACTIVITY, cold=False, run_index=2),
    ]
    failed = RunLatency(query="bad", total_ms=0.0, error="Boom: nope")
    runs.append(failed)

    report = aggregate_runs(runs, metadata={"mode": "mock"})
    assert report.run_count == 4
    assert report.error_count == 1
    assert report.total.count == 3  # failed run excluded from timing
    assert report.cold is not None and report.cold.count == 1
    assert report.warm is not None and report.warm.count == 2
    assert "searchIndex" in report.by_activity_type
    assert "agent_response" in report.by_stage


def test_identify_bottlenecks_flags_overhead_dominance():
    # Overhead dominates: total ~20s, activity ~3.7s → overhead ~16s.
    runs = [_make_run(20000, _LIVE_ACTIVITY, cold=(i == 0), run_index=i) for i in range(4)]
    report = aggregate_runs(runs)
    joined = " ".join(report.bottlenecks).lower()
    assert "overhead" in joined


def test_identify_bottlenecks_no_dominant():
    # Even spread across distinct contributors so none reaches the 40% share:
    # three activity types at 100 ms each (25% apiece) + 100 ms overhead (25%).
    run = RunLatency(
        query="q",
        total_ms=400,  # activity total 300 + 100 ms overhead, no single >= 40%
        stages=[],
        activity=parse_activity(
            [
                {"type": "modelQueryPlanning", "id": 0, "elapsedMs": 100},
                {"type": "searchIndex", "id": 1, "elapsedMs": 100},
                {"type": "semanticReranker", "id": 2, "elapsedMs": 100},
            ]
        ),
    )
    report = aggregate_runs([run])
    findings = identify_bottlenecks(report)
    assert any("evenly" in f for f in findings)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def test_render_markdown_contains_sections():
    runs = [_make_run(20000, _LIVE_ACTIVITY, cold=(i == 0), run_index=i) for i in range(3)]
    report = aggregate_runs(runs, metadata={"mode": "mock", "repeat": 3})
    md = render_markdown(report)
    assert "# Latency Investigation" in md
    assert "End-to-End Latency" in md
    assert "Service-Side Activity Breakdown" in md
    assert "Bottleneck Findings" in md


def test_render_markdown_contains_metric_glossary():
    runs = [_make_run(20000, _LIVE_ACTIVITY, cold=(i == 0), run_index=i) for i in range(3)]
    md = render_markdown(aggregate_runs(runs))
    assert "How to Read This Report" in md
    assert "p50 (median)" in md
    assert "p99" in md
    # cold vs warm must be defined
    assert "**cold**" in md and "**warm**" in md
    # overhead must be defined
    assert "overhead" in md.lower()


def test_stage_dominance_finding_targets_agent_response():
    # _make_run builds an agent_response stage at 80% of total → dominant.
    runs = [_make_run(20000, _LIVE_ACTIVITY, cold=(i == 0), run_index=i) for i in range(3)]
    report = aggregate_runs(runs)
    codes = {f.code for f in report.findings}
    assert "overhead_dominates" in codes
    assert "stage_agent_response" in codes
    stage_finding = next(f for f in report.findings if f.code == "stage_agent_response")
    joined = " ".join(stage_finding.mitigations).lower()
    # Accurate mitigation: not "persist agents" but reasoning/output-mode levers.
    assert "extractivedata" in joined or "reasoningeffort" in joined
    assert "agent_response" in stage_finding.message


def test_report_to_dict_is_json_serialisable():
    runs = [_make_run(20000, _LIVE_ACTIVITY, cold=(i == 0), run_index=i) for i in range(2)]
    report = aggregate_runs(runs)
    payload = report_to_dict(report)
    # Round-trips through json without error.
    text = json.dumps(payload)
    assert '"total"' in text
    assert payload["run_count"] == 2


def test_save_report_writes_files(tmp_path):
    runs = [_make_run(20000, _LIVE_ACTIVITY, run_index=i) for i in range(2)]
    report = aggregate_runs(runs)
    json_path, md_path = save_report(report, tmp_path, prefix="latency_test")
    assert json_path.exists() and md_path.exists()
    assert json.loads(json_path.read_text())["run_count"] == 2
    assert "Latency Investigation" in md_path.read_text()


# ---------------------------------------------------------------------------
# profile_orchestrator_call against a fake orchestrator
# ---------------------------------------------------------------------------

class _FakeOrchestrator:
    def __init__(self, activity, *, raise_exc=False, stage_timings=None):
        self._activity = activity
        self._raise = raise_exc
        self._stage_timings = stage_timings

    async def process_query_async(self, query: str):
        if self._raise:
            raise RuntimeError("simulated failure")
        out = {
            "answer": "ok",
            "activity": self._activity,
            "token_usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }
        if self._stage_timings is not None:
            out["stage_timings"] = self._stage_timings
        return out


@pytest.mark.asyncio
async def test_profile_orchestrator_call_success():
    orch = _FakeOrchestrator(_LIVE_ACTIVITY)
    run = await profile_orchestrator_call(orch, "q", run_index=2, cold_start=True)
    assert run.error is None
    assert run.total_ms > 0
    assert run.cold_start is True
    assert run.run_index == 2
    assert run.token_usage["total_tokens"] == 15
    assert run.activity_total_ms == 3520 + 118 + 78


@pytest.mark.asyncio
async def test_profile_orchestrator_call_merges_stage_timings():
    stages = [
        {"name": "client_setup", "duration_ms": 150.0, "metadata": {}},
        {"name": "agent_response", "duration_ms": 12000.0, "metadata": {"model": "gpt-5"}},
        {"name": "conversation_delete", "duration_ms": 200.0},
    ]
    orch = _FakeOrchestrator(_LIVE_ACTIVITY, stage_timings=stages)
    run = await profile_orchestrator_call(orch, "q")
    names = {s.name for s in run.stages}
    # "total" plus the orchestrator-reported stages.
    assert "total" in names
    assert "agent_response" in names and "client_setup" in names
    assert run.stage_ms("agent_response") == 12000.0


@pytest.mark.asyncio
async def test_profile_orchestrator_call_captures_error():
    orch = _FakeOrchestrator(None, raise_exc=True)
    run = await profile_orchestrator_call(orch, "q")
    assert run.error is not None
    assert "simulated failure" in run.error
    assert run.activity == []

