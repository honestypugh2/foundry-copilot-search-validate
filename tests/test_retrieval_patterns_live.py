#!/usr/bin/env python3
"""
Live End-to-End - Retrieval Pattern Comparison

NO mocks. Connects to live Azure AI Search and Azure AI Foundry.

Runs the SAME set of queries (best-suited for Agentic Retrieval, i.e.
multi-part / disambiguation queries that benefit from sub-query
decomposition) through three retrieval patterns:

    1. Direct Agentic Retrieval
       AzureAISearchClient.agentic_retrieve(...) - raw KB call,
       returns sub-queries (activity[]) + chunks (matches[]).

    2. agents/orchestrator_factory.get_orchestrator() (Pattern A default)
       FoundryAgentOrchestrator single-agent MCP pipeline.

    3. agents_af/sequential_orchestrator.SequentialWorkflowOrchestrator
       Agent Framework SequentialBuilder + FoundryChatClient.

For every query in every pattern the test logs:
    - Agents / executors invoked
    - Inputs and outputs of each stage
    - Token usage (prompt / completion / total)
    - Sub-queries decomposed by Agentic Retrieval
    - Chunks (references) used to answer
    - Final response

Usage:
    cd foundry-copilot-search-validate
    PYTHONPATH=$PWD/src python tests/test_retrieval_patterns_live.py

    # Limit to first N queries:
    PYTHONPATH=$PWD/src python tests/test_retrieval_patterns_live.py --limit 2

    # Run a single pattern only:
    PYTHONPATH=$PWD/src python tests/test_retrieval_patterns_live.py --pattern direct
    PYTHONPATH=$PWD/src python tests/test_retrieval_patterns_live.py --pattern factory
    PYTHONPATH=$PWD/src python tests/test_retrieval_patterns_live.py --pattern patternb
    PYTHONPATH=$PWD/src python tests/test_retrieval_patterns_live.py --pattern af

    # Run multiple patterns (comma-separated):
    PYTHONPATH=$PWD/src python tests/test_retrieval_patterns_live.py --pattern patternb,af
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


# ---- Logging setup ------------------------------------------------------
_log_dir = Path(__file__).resolve().parent.parent / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)
_log_file = _log_dir / "test_retrieval_patterns_live.log"

_plain_fmt = logging.Formatter("%(asctime)s  %(levelname)-8s  %(name)s  %(message)s")
_console = logging.StreamHandler(sys.stdout)
_console.setLevel(logging.INFO)
_console.setFormatter(_plain_fmt)
_fh = logging.FileHandler(_log_file, mode="a", encoding="utf-8")
_fh.setLevel(logging.INFO)
_fh.setFormatter(_plain_fmt)
logging.basicConfig(level=logging.INFO, handlers=[_console, _fh])
logger = logging.getLogger("retrieval_patterns")
logger.info("Log file: %s", _log_file)

# ---- Load .env ----------------------------------------------------------
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=True)
        logger.info("Loaded .env from %s", env_path)
except ImportError:
    pass

# ---- Test queries -------------------------------------------------------
from test_queries import QUERY_EXPECTATIONS  # noqa: E402

# Best-suited queries for Agentic Retrieval (decomposition / disambiguation)
AGENTIC_BEST_QUERY_IDS = [16, 17, 18, 32, 33]
AGENTIC_QUERIES = [qe for qe in QUERY_EXPECTATIONS if qe.query_id in AGENTIC_BEST_QUERY_IDS]


# =========================================================================
#  Helpers - verbose logging
# =========================================================================

def _log_subqueries(activity: list[dict[str, Any]], indent: str = "    ") -> None:
    """Pretty-print Agentic Retrieval sub-queries (activity[])."""
    if not activity:
        logger.info("%s(no activity / sub-queries returned)", indent)
        return

    logger.info("%sSub-queries (%d activity steps):", indent, len(activity))
    logger.info("%s  %-3s  %-50s  %-7s  %-10s  %s",
                indent, "#", "Sub-query / detail", "Hits", "Elapsed", "Type")
    logger.info("%s  %s", indent, "-" * 95)
    for i, act in enumerate(activity, 1):
        atype = act.get("type", "?")
        # Activity payload is camelCase (REST 2026-04-01)
        search_args = (
            act.get("searchIndexArguments")
            or act.get("search_index_arguments")
            or {}
        )
        sub = (
            search_args.get("search")
            or act.get("query")
        )
        if not sub and atype == "agenticReasoning":
            rt = act.get("reasoningTokens", act.get("reasoning_tokens"))
            sub = f"(reasoning, tokens={rt})" if rt is not None else "(reasoning)"
        sub_str = str(sub) if sub else ""
        if len(sub_str) > 47:
            sub_str = sub_str[:47] + "..."
        count = act.get("count", "")
        elapsed = act.get("elapsedMs", act.get("elapsed_ms", ""))
        logger.info("%s  %-3d  %-50s  %-7s  %-10s  %s",
                    indent, i, sub_str, count, elapsed, atype)


def _log_chunks(matches: list[dict[str, Any]], limit: int = 5,
                indent: str = "    ") -> None:
    """Pretty-print the chunks/references used as grounding."""
    if not matches:
        logger.info("%s(no chunks returned)", indent)
        return

    logger.info("%sChunks used (showing top %d of %d):",
                indent, min(limit, len(matches)), len(matches))
    for i, m in enumerate(matches[:limit], 1):
        policy = m.get("policyNumber", "")
        fname = m.get("fileName", "") or m.get("parentTitle", "")
        score = m.get("reranker_score", m.get("score", ""))
        snippet = (m.get("content", "") or "").strip().replace("\n", " ")
        if len(snippet) > 140:
            snippet = snippet[:140] + "..."
        logger.info("%s  [%d] policy=%s  file=%s  score=%s",
                    indent, i, policy, (fname or "")[:70], score)
        if snippet:
            logger.info("%s      snippet: %s", indent, snippet)


def _log_tokens(token_usage: dict[str, Any], indent: str = "    ") -> None:
    if not token_usage:
        logger.info("%sTokens: (not reported)", indent)
        return
    p = token_usage.get("prompt_tokens", 0)
    c = token_usage.get("completion_tokens", 0)
    t = token_usage.get("total_tokens", 0)
    logger.info("%sTokens: prompt=%s  completion=%s  total=%s", indent, p, c, t)


def _log_answer(answer: str, indent: str = "    ", limit: int = 600) -> None:
    if not answer:
        logger.info("%sAnswer: (empty)", indent)
        return
    txt = answer.strip().replace("\n", "\n" + indent + "        ")
    if len(answer) > limit:
        txt = txt[:limit] + "  ... [truncated]"
    logger.info("%sAnswer (%d chars):", indent, len(answer))
    logger.info("%s    %s", indent, txt)


# =========================================================================
#  Pattern 1: Direct Agentic Retrieval
# =========================================================================

def run_pattern_direct_agentic(qe) -> dict[str, Any]:
    """Direct call to AzureAISearchClient.agentic_retrieve - shows sub-queries
    and chunks before any agent is involved."""
    from search.azure_ai_search_client import AzureAISearchClient

    logger.info("    Agent invoked  : Azure AI Search Knowledge Base")
    logger.info("    Method         : agentic_retrieve (REST 2026-04-01)")
    logger.info("    Input          : query=%r", qe.query)

    client = AzureAISearchClient()
    t0 = time.time()
    res = client.agentic_retrieve(messages=[{"role": "user", "content": qe.query}])
    elapsed = time.time() - t0

    activity = res.get("activity", []) or []
    matches = res.get("matches", []) or []
    response_text = res.get("response", "") or ""

    logger.info("    Output         : status=ok  elapsed=%.2fs", elapsed)
    _log_subqueries(activity)
    _log_chunks(matches)
    _log_tokens({}, indent="    ")  # KB REST does not return tokens
    _log_answer(response_text)

    return {
        "pattern": "direct_agentic",
        "query": qe.query,
        "expected_policy": qe.expected_policy,
        "elapsed": elapsed,
        "answer": response_text,
        "matches_count": len(matches),
        "activity_count": len(activity),
        "subqueries": [
            (a.get("searchIndexArguments") or a.get("search_index_arguments") or {})
            .get("search", a.get("query", ""))
            for a in activity if a.get("type") == "searchIndex"
        ],
        "reasoning_tokens": sum(
            a.get("reasoningTokens", a.get("reasoning_tokens", 0)) or 0
            for a in activity if a.get("type") == "agenticReasoning"
        ),
        "chunks": [
            {
                "policyNumber": m.get("policyNumber", ""),
                "fileName": m.get("fileName", ""),
                "reranker_score": m.get("reranker_score"),
                "snippet": (m.get("content", "") or "")[:200],
            }
            for m in matches
        ],
        "token_usage": {},
    }


# =========================================================================
#  Pattern 2: agents/orchestrator_factory (default Pattern A: single-agent MCP)
# =========================================================================

def run_pattern_orchestrator_factory(qe) -> dict[str, Any]:
    os.environ.setdefault("ORCHESTRATOR_PATTERN", "A")
    os.environ.setdefault("VALIDATE_CITATIONS", "true")

    from agents.orchestrator_factory import get_orchestrator

    logger.info("    Agents invoked : FoundryAgentOrchestrator (Pattern A)")
    logger.info("                     - HRPolicyAgent (Foundry PromptAgent + MCPTool)")
    logger.info("                     - knowledge-base MCP server (tool_choice=required)")
    logger.info("    Input          : query=%r", qe.query)

    orch = get_orchestrator()
    if hasattr(orch, "_validate_citations"):
        orch._validate_citations = True

    t0 = time.time()
    result = orch.process_query(qe.query)
    elapsed = time.time() - t0

    answer = result.get("answer", "") or ""
    activity = result.get("activity", []) or []
    token_usage = result.get("token_usage", {}) or {}
    citations = result.get("citation_validation", {}) or {}
    steps = result.get("steps", {}) or {}

    logger.info("    Output         : status=%s  elapsed=%.2fs",
                result.get("status", "?"), elapsed)
    logger.info("    Pipeline mode  : %s", result.get("pipeline_mode", "?"))
    logger.info("    Output mode    : %s", result.get("output_mode", "?"))
    logger.info("    Steps          : %s", list(steps.keys()))
    for step_name, step_data in steps.items():
        logger.info("      [step] %-32s -> %s", step_name, step_data)

    _log_subqueries(activity)

    # Single-agent MCP path keeps chunks behind the MCP boundary; the
    # chunks visible to us are those captured by the parallel agentic_retrieve
    # call inside the orchestrator (no matches dict). When multi_step mode,
    # matches are returned at top-level.
    matches = result.get("matches", []) or []
    if matches:
        _log_chunks(matches)
    else:
        logger.info("    Chunks         : delegated to MCP tool inside agent "
                    "(see 'subqueries' for retrieval transparency)")

    _log_tokens(token_usage)

    if citations:
        logger.info("    Citations      : has_citations=%s  count=%s",
                    citations.get("has_citations"), citations.get("citation_count"))
        for c in citations.get("citations", [])[:5]:
            logger.info("      cite -> msg=%s search=%s source=%s",
                        c.get("message_idx"), c.get("search_idx"), c.get("source_name"))

    _log_answer(answer)

    return {
        "pattern": "orchestrator_factory_A",
        "query": qe.query,
        "expected_policy": qe.expected_policy,
        "elapsed": elapsed,
        "status": result.get("status"),
        "answer": answer,
        "pipeline_mode": result.get("pipeline_mode"),
        "output_mode": result.get("output_mode"),
        "steps": list(steps.keys()),
        "subqueries": [
            (a.get("searchIndexArguments") or a.get("search_index_arguments") or {})
            .get("search", a.get("query", ""))
            for a in activity if a.get("type") == "searchIndex"
        ],
        "reasoning_tokens": sum(
            a.get("reasoningTokens", a.get("reasoning_tokens", 0)) or 0
            for a in activity if a.get("type") == "agenticReasoning"
        ),
        "chunks": [
            {
                "policyNumber": m.get("policyNumber", ""),
                "fileName": m.get("fileName", ""),
                "reranker_score": m.get("reranker_score"),
                "snippet": (m.get("content", "") or "")[:200],
            }
            for m in matches
        ],
        "token_usage": token_usage,
        "citations": citations,
    }


# =========================================================================
#  Pattern 2b: agents/orchestrator_pattern_b (Hybrid MCP + Metadata Lookup)
# =========================================================================

def run_pattern_orchestrator_b(qe) -> dict[str, Any]:
    """agents/orchestrator_pattern_b.PatternBOrchestrator.

    Client-side classifier routes:
      - file-location queries -> file_metadata_lookup() (direct AI Search,
        deterministic file paths)
      - content queries       -> Foundry Agent + MCPTool (knowledge_base_retrieve)
    """
    os.environ["ORCHESTRATOR_PATTERN"] = "B"
    os.environ.setdefault("VALIDATE_CITATIONS", "true")

    # Force re-import so the env var is picked up
    import importlib
    import agents.orchestrator_factory as _of
    importlib.reload(_of)

    logger.info("    Agents invoked : PatternBOrchestrator (agents/orchestrator_pattern_b)")
    logger.info("                     - Client-side classifier (regex) routes:")
    logger.info("                         file-location -> file_metadata_lookup (direct AI Search)")
    logger.info("                         content       -> HRPolicyAgentB (Foundry PromptAgent + MCPTool)")
    logger.info("    Input          : query=%r", qe.query)

    orch = _of.get_orchestrator()
    if hasattr(orch, "_validate_citations"):
        orch._validate_citations = True

    t0 = time.time()
    result = orch.process_query(qe.query)
    elapsed = time.time() - t0

    answer = result.get("answer", "") or ""
    activity = result.get("activity", []) or []
    token_usage = result.get("token_usage", {}) or {}
    citations = result.get("citation_validation", {}) or {}
    tool_calls = result.get("tool_calls", []) or []

    logger.info("    Output         : status=%s  elapsed=%.2fs",
                result.get("status", "?"), elapsed)
    logger.info("    Pipeline mode  : %s", result.get("pipeline_mode", "?"))
    logger.info("    Output mode    : %s", result.get("output_mode", "?"))
    logger.info("    Pattern        : %s", result.get("pattern", "?"))
    logger.info("    Model          : %s", result.get("model", "?"))
    logger.info("    Grounded       : %s", result.get("is_grounded"))

    # Routing transparency: which tool was actually executed
    if tool_calls:
        logger.info("    Tool calls     : %d", len(tool_calls))
        for i, tc in enumerate(tool_calls, 1):
            tname = tc.get("tool", "?")
            args = tc.get("arguments", {})
            tres = tc.get("result", {}) or {}
            found = tres.get("found")
            doc_count = tres.get("document_count", len(tres.get("documents", []) or []))
            logger.info("      [tc %d] tool=%s  args=%s  found=%s  documents=%s",
                        i, tname, args, found, doc_count)
            # Surface the documents/chunks returned by the tool
            for j, doc in enumerate((tres.get("documents", []) or [])[:5], 1):
                pol = doc.get("policy_number") or doc.get("policyNumber") or ""
                fname = (
                    doc.get("metadata_storage_name")
                    or doc.get("fileName")
                    or doc.get("parent_title")
                    or ""
                )
                path = doc.get("metadata_storage_path") or doc.get("filePath") or ""
                score = doc.get("reranker_score")
                snippet = (doc.get("content", "") or "").strip().replace("\n", " ")
                if len(snippet) > 140:
                    snippet = snippet[:140] + "..."
                logger.info("          [%d] policy=%s  file=%s  score=%s",
                            j, pol, fname[:70], score)
                if path:
                    logger.info("              path: %s", path[:140])
                if snippet:
                    logger.info("              snippet: %s", snippet)
    else:
        logger.info("    Tool calls     : (none recorded)")

    _log_subqueries(activity)
    _log_tokens(token_usage)

    if citations:
        logger.info("    Citations      : has_citations=%s  count=%s",
                    citations.get("has_citations"), citations.get("citation_count"))
        for c in citations.get("citations", [])[:5]:
            logger.info("      cite -> msg=%s search=%s source=%s",
                        c.get("message_idx"), c.get("search_idx"), c.get("source_name"))

    _log_answer(answer)

    # Build chunks list out of the tool_call results so the JSON has them
    chunks: list[dict[str, Any]] = []
    for tc in tool_calls:
        tres = tc.get("result", {}) or {}
        for doc in (tres.get("documents", []) or []):
            chunks.append({
                "tool": tc.get("tool"),
                "policyNumber": doc.get("policy_number") or doc.get("policyNumber", ""),
                "fileName": (
                    doc.get("metadata_storage_name")
                    or doc.get("fileName", "")
                ),
                "metadata_storage_path": doc.get("metadata_storage_path", ""),
                "blob_url": doc.get("blob_url", ""),
                "reranker_score": doc.get("reranker_score"),
                "snippet": (doc.get("content", "") or "")[:200],
            })

    return {
        "pattern": "orchestrator_pattern_b",
        "query": qe.query,
        "expected_policy": qe.expected_policy,
        "elapsed": elapsed,
        "status": result.get("status"),
        "answer": answer,
        "pipeline_mode": result.get("pipeline_mode"),
        "output_mode": result.get("output_mode"),
        "model": result.get("model"),
        "tool_calls": [
            {
                "tool": tc.get("tool"),
                "arguments": tc.get("arguments"),
                "found": (tc.get("result") or {}).get("found"),
                "document_count": (
                    (tc.get("result") or {}).get("document_count")
                    or len((tc.get("result") or {}).get("documents") or [])
                ),
            }
            for tc in tool_calls
        ],
        "subqueries": [
            (a.get("searchIndexArguments") or a.get("search_index_arguments") or {})
            .get("search", a.get("query", ""))
            for a in activity if a.get("type") == "searchIndex"
        ],
        "reasoning_tokens": sum(
            a.get("reasoningTokens", a.get("reasoning_tokens", 0)) or 0
            for a in activity if a.get("type") == "agenticReasoning"
        ),
        "chunks": chunks,
        "token_usage": token_usage,
        "citations": citations,
    }


# =========================================================================
#  Pattern 3: agents_af/sequential_orchestrator
# =========================================================================

def run_pattern_agent_framework(qe) -> dict[str, Any]:
    from agents_af.sequential_orchestrator import (
        SequentialWorkflowOrchestrator,
        PIPELINE_AGENTS,
    )

    logger.info("    Agents invoked : SequentialWorkflowOrchestrator (Agent Framework)")
    for name, cfg in PIPELINE_AGENTS.items():
        logger.info("                     - %s", name)
    logger.info("                     Executors: retrieval -> source_validation -> "
                "reference_validation -> answer_synthesis -> FoundryChatClient -> "
                "final_synthesis")
    logger.info("    Input          : query=%r", qe.query)

    orch = SequentialWorkflowOrchestrator()
    t0 = time.time()
    result = orch.process_query(qe.query)
    elapsed = time.time() - t0

    answer = result.get("answer", "") or ""
    matches = result.get("matches", []) or []
    activity = result.get("activity", []) or []
    references = result.get("references", []) or []
    validated_sources = result.get("validated_sources", []) or []
    steps = result.get("steps", {}) or {}
    token_usage = result.get("token_usage", {}) or {}

    logger.info("    Output         : status=%s  elapsed=%.2fs",
                result.get("status", "?"), elapsed)
    logger.info("    Steps          : %s", list(steps.keys()))
    for step_name, step_data in steps.items():
        logger.info("      [step] %-32s -> %s", step_name, step_data)

    logger.info("    Matches        : %d", len(matches))
    logger.info("    Validated      : %d", len(validated_sources))
    logger.info("    References     : %d", len(references))
    logger.info("    Grounded       : %s", result.get("is_grounded"))

    _log_subqueries(activity)
    _log_chunks(matches)
    _log_tokens(token_usage)
    _log_answer(answer)

    return {
        "pattern": "agent_framework",
        "query": qe.query,
        "expected_policy": qe.expected_policy,
        "elapsed": elapsed,
        "status": result.get("status"),
        "answer": answer,
        "is_grounded": result.get("is_grounded"),
        "steps": list(steps.keys()),
        "subqueries": [
            (a.get("searchIndexArguments") or a.get("search_index_arguments") or {})
            .get("search", a.get("query", ""))
            for a in activity if a.get("type") == "searchIndex"
        ],
        "reasoning_tokens": sum(
            a.get("reasoningTokens", a.get("reasoning_tokens", 0)) or 0
            for a in activity if a.get("type") == "agenticReasoning"
        ),
        "chunks": [
            {
                "policyNumber": m.get("policyNumber", ""),
                "fileName": m.get("fileName", ""),
                "reranker_score": m.get("reranker_score"),
                "snippet": (m.get("content", "") or "")[:200],
            }
            for m in matches
        ],
        "validated_count": len(validated_sources),
        "references_count": len(references),
        "token_usage": token_usage,
    }


# =========================================================================
#  Test driver
# =========================================================================

PATTERNS = {
    "direct":   ("Direct Agentic Retrieval",                      run_pattern_direct_agentic),
    "factory":  ("agents/orchestrator_factory (A)",               run_pattern_orchestrator_factory),
    "patternb": ("agents/orchestrator_pattern_b (B)",             run_pattern_orchestrator_b),
    "af":       ("agents_af/sequential_orchestrator",             run_pattern_agent_framework),
}


def run_query_all_patterns(qe, patterns_to_run: list[str]) -> dict[str, Any]:
    bundle: dict[str, Any] = {
        "query_id": qe.query_id,
        "query": qe.query,
        "expected_policy": qe.expected_policy,
        "expected_file": qe.expected_file,
        "description": qe.description,
        "results": {},
    }

    for pkey in patterns_to_run:
        title, runner = PATTERNS[pkey]
        logger.info("")
        logger.info("  +-- Pattern: %s --+", title)
        try:
            bundle["results"][pkey] = runner(qe)
        except Exception as e:
            logger.exception("    EXCEPTION in pattern %s: %s", pkey, e)
            bundle["results"][pkey] = {
                "pattern": pkey, "error": str(e), "error_type": type(e).__name__,
            }

    return bundle


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=None,
                        help="Limit number of queries (default: all 5)")
    parser.add_argument("--pattern", default="all",
                        help=("Which pattern(s) to run. One of: "
                              + ", ".join(list(PATTERNS) + ["all"]) +
                              ". Comma-separated list also accepted, "
                              "e.g. 'patternb,af'."))
    parser.add_argument("--query-id", type=int, default=None,
                        help="Run a single query by id")
    args = parser.parse_args()

    queries = AGENTIC_QUERIES
    if args.query_id is not None:
        queries = [qe for qe in QUERY_EXPECTATIONS if qe.query_id == args.query_id]
        if not queries:
            logger.error("No query with id=%s", args.query_id)
            sys.exit(2)
    if args.limit:
        queries = queries[:args.limit]

    if args.pattern == "all":
        patterns_to_run = list(PATTERNS)
    else:
        patterns_to_run = [p.strip() for p in args.pattern.split(",") if p.strip()]
        bad = [p for p in patterns_to_run if p not in PATTERNS]
        if bad:
            logger.error("Unknown pattern(s): %s. Valid: %s",
                         bad, list(PATTERNS) + ["all"])
            sys.exit(2)

    logger.info("=" * 78)
    logger.info("RETRIEVAL PATTERN COMPARISON - LIVE")
    logger.info("=" * 78)
    logger.info("Queries  : %d (best-suited for Agentic Retrieval)", len(queries))
    logger.info("Patterns : %s", ", ".join(patterns_to_run))
    logger.info("Log file : %s", _log_file)
    logger.info("")

    results_path = Path(__file__).resolve().parent / "test_retrieval_patterns_results.json"
    all_bundles = []

    for i, qe in enumerate(queries, 1):
        logger.info("=" * 78)
        logger.info("QUERY %d/%d  [id=%d]  %s", i, len(queries), qe.query_id, qe.query)
        logger.info("Expected: policy=%s  file=%s",
                    qe.expected_policy, qe.expected_file[:60])
        logger.info("=" * 78)

        bundle = run_query_all_patterns(qe, patterns_to_run)
        all_bundles.append(bundle)

        # Write incrementally so partial runs are still useful
        with open(results_path, "w") as f:
            json.dump(all_bundles, f, indent=2, default=str)

    # ---- Summary -------------------------------------------------------
    logger.info("")
    logger.info("=" * 78)
    logger.info("SUMMARY")
    logger.info("=" * 78)
    logger.info("%-3s  %-60s  %-12s  %-12s  %s",
                "ID", "Query", "Pattern", "Elapsed", "Subqueries / Matches")
    logger.info("-" * 110)
    for b in all_bundles:
        for pkey in patterns_to_run:
            r = b["results"].get(pkey, {})
            if "error" in r:
                logger.info("%-3d  %-60s  %-12s  %-12s  ERROR: %s",
                            b["query_id"], b["query"][:60], pkey, "-",
                            r["error"][:60])
                continue
            elapsed = r.get("elapsed", 0)
            sq_count = len(r.get("subqueries", []))
            mc = r.get("matches_count", len(r.get("chunks", [])))
            logger.info("%-3d  %-60s  %-12s  %.2fs%-7s  subq=%d  chunks=%s",
                        b["query_id"], b["query"][:60], pkey, elapsed, "",
                        sq_count, mc)

    logger.info("")
    logger.info("Results written to: %s", results_path)
    logger.info("Log written to    : %s", _log_file)


if __name__ == "__main__":
    main()
