"""
Rendering and persistence for latency reports.

* :func:`report_to_dict`  – JSON-serialisable dict of a :class:`LatencyReport`.
* :func:`render_markdown` – human-readable Markdown summary.
* :func:`save_report`     – write ``latency_<timestamp>.json`` + ``.md``.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from latency.models import LatencyReport, LatencyStats


def _stats_row(s: LatencyStats) -> str:
    return (
        f"| {s.label} | {s.count} | {s.mean_ms:.0f} | {s.p50_ms:.0f} | "
        f"{s.p90_ms:.0f} | {s.p95_ms:.0f} | {s.p99_ms:.0f} | "
        f"{s.min_ms:.0f} | {s.max_ms:.0f} | {s.stdev_ms:.0f} |"
    )


def _glossary_lines() -> list[str]:
    """Definitions for the metrics and groupings used throughout the report."""
    return [
        "## How to Read This Report",
        "",
        "**Latency metrics** (all values in milliseconds, lower is better):",
        "",
        "- **N** — number of successful runs in the sample.",
        "- **mean** — arithmetic average; sensitive to outliers.",
        "- **p50 (median)** — 50% of requests were at or below this. The "
        "_typical_ experience.",
        "- **p90** — 90% of requests were at or below this; the slowest 1 in "
        "10 are worse.",
        "- **p95** — 95th percentile; the slowest 1 in 20 are worse.",
        "- **p99** — 99th percentile; the worst 1 in 100. Captures the _tail_ "
        "that frustrates users.",
        "- **min / max** — fastest and slowest single run observed.",
        "- **stdev** — standard deviation; how spread out the times are. "
        "Higher = less predictable.",
        "",
        "Percentiles are preferred over the mean because latency is usually "
        "skewed: a few slow requests inflate the average, while p50/p95/p99 "
        "describe what real users actually feel.",
        "",
        "**Timing layers:**",
        "",
        "- **total** — end-to-end wall-clock for the whole request "
        "(`process_query_async`).",
        "- **activity_total** — time the Azure AI Search service reports inside "
        "its `activity[]` trace (query planning + index search + reranking).",
        "- **overhead** — `total − activity_total`: everything not in the "
        "service trace (client SDK, Foundry agent/conversation control plane, "
        "network, and LLM answer-synthesis streaming).",
        "",
        "**Cold vs. warm:**",
        "",
        "- **cold** — the _first_ run against an idle agent/host. It pays "
        "one-time setup costs: agent creation/lookup, auth-token acquisition, "
        "TLS/connection setup, and model load.",
        "- **warm** — every subsequent run, where those caches/connections are "
        "already in place. Comparing cold vs. warm isolates start-up penalty "
        "from steady-state latency.",
        "",
        "**Client-side stages** (from the orchestrator's own timers): "
        "`client_setup`, `agent_ensure`, `conversation_create`, "
        "`agent_response` (LLM reasoning + MCP tool + answer streaming), "
        "`activity_capture`, `conversation_delete`.",
        "",
    ]


def report_to_dict(report: LatencyReport) -> dict:
    """Return a JSON-serialisable representation of the report."""
    return {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "run_count": report.run_count,
        "error_count": report.error_count,
        "metadata": report.metadata,
        "total": asdict(report.total),
        "overhead": asdict(report.overhead),
        "activity_total": asdict(report.activity_total),
        "by_stage": {k: asdict(v) for k, v in report.by_stage.items()},
        "by_activity_type": {
            k: asdict(v) for k, v in report.by_activity_type.items()
        },
        "cold": asdict(report.cold) if report.cold else None,
        "warm": asdict(report.warm) if report.warm else None,
        "bottlenecks": report.bottlenecks,
        "findings": [asdict(f) for f in report.findings],
        "runs": [
            {
                "query": r.query,
                "run_index": r.run_index,
                "cold_start": r.cold_start,
                "total_ms": round(r.total_ms, 1),
                "activity_total_ms": round(r.activity_total_ms, 1),
                "overhead_ms": round(r.overhead_ms, 1),
                "token_usage": r.token_usage,
                "activity_ms_by_type": {
                    k: round(v, 1) for k, v in r.activity_ms_by_type().items()
                },
                "error": r.error,
            }
            for r in report.runs
        ],
    }


def render_markdown(report: LatencyReport) -> str:
    """Render a Markdown summary of the latency investigation."""
    lines: list[str] = []
    lines.append("# Latency Investigation — Foundry Agentic Retrieval")
    lines.append("")
    lines.append(f"_Generated: {datetime.now(timezone.utc).isoformat()}_")
    lines.append("")
    if report.metadata:
        meta = ", ".join(f"{k}={v}" for k, v in report.metadata.items())
        lines.append(f"**Configuration:** {meta}")
        lines.append("")
    lines.append(
        f"**Runs:** {report.run_count}  |  **Errors:** {report.error_count}"
    )
    lines.append("")

    # Glossary so the tables below are self-explanatory.
    lines.extend(_glossary_lines())

    # Headline percentile table.
    header = (
        "| Metric | N | mean | p50 | p90 | p95 | p99 | min | max | stdev |\n"
        "|--------|--:|-----:|----:|----:|----:|----:|----:|----:|------:|"
    )
    lines.append("## End-to-End Latency (ms)")
    lines.append("")
    lines.append(header)
    lines.append(_stats_row(report.total))
    lines.append(_stats_row(report.activity_total))
    lines.append(_stats_row(report.overhead))
    lines.append("")

    # Service-side breakdown.
    if report.by_activity_type:
        lines.append("## Service-Side Activity Breakdown (ms)")
        lines.append("")
        lines.append(header)
        for stats in sorted(
            report.by_activity_type.values(), key=lambda s: s.p50_ms, reverse=True
        ):
            lines.append(_stats_row(stats))
        lines.append("")

    # Client-side stage breakdown (only when instrumented).
    if report.by_stage:
        lines.append("## Client-Side Stage Breakdown (ms)")
        lines.append("")
        lines.append(header)
        for stats in sorted(
            report.by_stage.values(), key=lambda s: s.p50_ms, reverse=True
        ):
            lines.append(_stats_row(stats))
        lines.append("")

    # Cold vs warm.
    if report.cold or report.warm:
        lines.append("## Cold vs. Warm (ms)")
        lines.append("")
        lines.append(header)
        if report.cold:
            lines.append(_stats_row(report.cold))
        if report.warm:
            lines.append(_stats_row(report.warm))
        lines.append("")

    # Findings.
    lines.append("## Bottleneck Findings")
    lines.append("")
    if report.findings:
        for i, f in enumerate(report.findings, start=1):
            lines.append(f"### {i}. {f.message}")
            lines.append("")
            lines.append(f"**What it means:** {f.definition}")
            lines.append("")
            lines.append("**Mitigations:**")
            for m in f.mitigations:
                lines.append(f"- {m}")
            lines.append("")
    else:
        for finding in report.bottlenecks:
            lines.append(f"- {finding}")
        lines.append("")

    return "\n".join(lines)


def save_report(
    report: LatencyReport,
    out_dir: str | Path,
    *,
    prefix: str = "latency",
) -> tuple[Path, Path]:
    """Write the report as ``<prefix>_<timestamp>.json`` and ``.md``.

    Returns the ``(json_path, md_path)`` tuple. Creates ``out_dir`` if needed.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    json_path = out / f"{prefix}_{ts}.json"
    md_path = out / f"{prefix}_{ts}.md"
    json_path.write_text(json.dumps(report_to_dict(report), indent=2), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path
