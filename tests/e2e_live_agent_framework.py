#!/usr/bin/env python3
"""
Live End-to-End – Agent Framework + FoundryChatClient Path (agents_af/)

NO mocks. Connects to live Azure AI Search, Azure AI Foundry, and
FoundryChatClient.  Every query runs the full sequential pipeline:

    Azure AI Search (Agentic Retrieval)
        → RetrievalExecutor           (custom Executor)
        → SourceValidationExecutor    (custom Executor)
        → ReferenceValidationExecutor (custom Executor)
        → AnswerSynthesisExecutor     (custom Executor)
        → FoundryChatClient Agent     (answer synthesis via Foundry)
        → FinalSynthesisExecutor      (custom Executor)

Verifies that the FoundryChatClient Agent appears in the Azure AI
Foundry portal by checking agent registration.

Usage:
    cd foundry-copilot-search-validate
    PYTHONPATH=$PWD/src python tests/e2e_live_agent_framework.py

Environment variables required (from src/.env):
    AZURE_SEARCH_ENDPOINT
    AZURE_AI_PROJECT_ENDPOINT  (or AZURE_AI_FOUNDRY_PROJECT_ENDPOINT)
    FOUNDRY_MODEL              (default: gpt-4.1)
"""

import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))


# ─── Colored formatter for terminal output ───────────────────────────────
class ColorFormatter(logging.Formatter):
    """ANSI-colored log formatter keyed on level and message content."""

    GREY = "\033[38;5;245m"
    BLUE = "\033[38;5;39m"
    CYAN = "\033[38;5;87m"
    GREEN = "\033[38;5;82m"
    YELLOW = "\033[38;5;220m"
    RED = "\033[38;5;196m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"

    LEVEL_COLORS = {
        logging.DEBUG: GREY,
        logging.INFO: CYAN,
        logging.WARNING: YELLOW,
        logging.ERROR: RED,
        logging.CRITICAL: RED + BOLD,
    }

    def format(self, record: logging.LogRecord) -> str:
        msg = super().format(record)
        color = self.LEVEL_COLORS.get(record.levelno, self.RESET)

        MAGENTA = "\033[38;5;213m"
        WHITE = "\033[38;5;255m"

        # Stage headers (═══ lines, STEP lines)
        if "═" in msg or "STEP" in msg.upper() and ":" in msg:
            return f"{self.BOLD}{self.BLUE}{msg}{self.RESET}"
        # Banner box
        if any(c in msg for c in ("╔", "╗", "║", "╚", "╝")):
            return f"{self.BOLD}{self.BLUE}{msg}{self.RESET}"
        # Query banners (▶ new query)
        if "▶" in msg:
            return f"{self.BOLD}{MAGENTA}{msg}{self.RESET}"
        # Input labels
        if "⮞ INPUT" in msg:
            return f"{WHITE}{msg}{self.RESET}"
        # Output labels
        if "⮜ OUTPUT" in msg:
            return f"{self.BOLD}{self.GREEN}{msg}{self.RESET}"
        # Passed / success
        if "✓" in msg or "PASSED" in msg:
            return f"{self.GREEN}{msg}{self.RESET}"
        # Warnings
        if "⚠" in msg or record.levelno == logging.WARNING:
            return f"{self.YELLOW}{msg}{self.RESET}"
        # Failures
        if "✗" in msg or "FAILED" in msg or record.levelno >= logging.ERROR:
            return f"{self.RED}{msg}{self.RESET}"
        # Query / section separators
        if "───" in msg:
            return f"{self.DIM}{self.CYAN}{msg}{self.RESET}"
        # SUMMARY / FINAL lines
        if "SUMMARY" in msg or "FINAL" in msg:
            return f"{self.BOLD}{msg}{self.RESET}"

        return f"{color}{msg}{self.RESET}"


# ─── Logging setup: console (colored) + file (plain) ────────────────────
_log_dir = Path(__file__).resolve().parent.parent / "logs"
_log_dir.mkdir(parents=True, exist_ok=True)
_log_file = _log_dir / "e2e_live_agent_framework.log"

_plain_fmt = logging.Formatter(
    "%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"
)

# Console handler — colored
_console = logging.StreamHandler(sys.stdout)
_console.setLevel(logging.INFO)
_console.setFormatter(ColorFormatter("%(asctime)s  %(levelname)-8s  %(name)s  %(message)s"))

# File handler — plain text
_fh = logging.FileHandler(_log_file, mode="w", encoding="utf-8")
_fh.setLevel(logging.INFO)
_fh.setFormatter(_plain_fmt)

logging.basicConfig(level=logging.INFO, handlers=[_console, _fh])


# Suppress "Event loop is closed" errors from httpx AsyncClient cleanup
# (triggered when async transports are GC'd after the event loop closes)
class _EventLoopClosedFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if "Event loop is closed" in record.getMessage():
            return False
        return True


logging.getLogger("asyncio").addFilter(_EventLoopClosedFilter())

logger = logging.getLogger("e2e_live_af")
logger.info("Log file: %s", _log_file)

# ─── Load .env ───────────────────────────────────────────────────────────
try:
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path, override=True)
        logger.info("Loaded .env from %s", env_path)
except ImportError:
    pass

# ─── Import shared test queries ──────────────────────────────────────────
from test_queries import TEST_QUERIES, QUERY_EXPECTATIONS


# =========================================================================
#  STEP 1: Verify Azure credentials and environment
# =========================================================================

def step_1_verify_environment() -> dict[str, str]:
    logger.info("=" * 70)
    logger.info("STEP 1: Verify Azure environment")
    logger.info("=" * 70)

    logger.info("  ⮞ INPUT: environment variables + Azure credential")

    required_vars = {
        "AZURE_SEARCH_ENDPOINT": os.getenv("AZURE_SEARCH_ENDPOINT", ""),
        "AZURE_AI_PROJECT_ENDPOINT": (
            os.getenv("AZURE_AI_PROJECT_ENDPOINT")
            or os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", "")
        ),
    }

    for key, val in required_vars.items():
        assert val, f"{key} is not set"
        logger.info("  ✓ %s = %s...", key, val[:45])

    model = os.getenv("FOUNDRY_MODEL") or os.getenv(
        "AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4.1"
    )
    logger.info("  ✓ MODEL = %s", model)

    # Verify credential
    from azure.identity import DefaultAzureCredential
    cred = DefaultAzureCredential()
    token = cred.get_token("https://cognitiveservices.azure.com/.default")
    assert token.token, "Failed to acquire Azure token"
    logger.info("  ✓ DefaultAzureCredential acquired token")

    logger.info("  ⮜ OUTPUT: Azure endpoint=%s, model=%s, credential=OK",
                 required_vars["AZURE_SEARCH_ENDPOINT"][:30] + "...", model)
    logger.info("✓ Step 1 PASSED\n")
    return required_vars


# =========================================================================
#  STEP 2: Verify Agent Framework + FoundryChatClient imports
# =========================================================================

def step_2_verify_imports() -> None:
    logger.info("=" * 70)
    logger.info("STEP 2: Verify Agent Framework imports")
    logger.info("=" * 70)

    logger.info("  ⮞ INPUT: import agent_framework, agents_af modules")

    from agent_framework import Agent, Executor, Message
    from agent_framework.foundry import FoundryChatClient
    from agent_framework.orchestrations import SequentialBuilder

    from agents_af.retrieval_agent import RetrievalAgent
    from agents_af.source_validator_agent import SourceValidatorAgent
    from agents_af.reference_validator_agent import ReferenceValidatorAgent
    from agents_af.answer_synthesis_agent import AnswerSynthesisAgent
    from agents_af.sequential_orchestrator import SequentialWorkflowOrchestrator

    logger.info("  ✓ agent_framework core: Agent, Executor, Message")
    logger.info("  ✓ FoundryChatClient")
    logger.info("  ✓ SequentialBuilder")
    logger.info("  ✓ All agents_af/ agents")
    logger.info("  ✓ SequentialWorkflowOrchestrator (AF)")
    logger.info("  ⮜ OUTPUT: 5 modules imported successfully")
    logger.info("✓ Step 2 PASSED\n")


# =========================================================================
#  STEP 3: Verify FoundryChatClient Agent registers in Foundry Portal
# =========================================================================

def step_3_verify_agent_registration() -> None:
    logger.info("=" * 70)
    logger.info("STEP 3: Verify all pipeline agents → Foundry Portal")
    logger.info("=" * 70)

    from azure.identity import DefaultAzureCredential
    from azure.ai.projects import AIProjectClient
    from azure.ai.projects.models import PromptAgentDefinition
    from agents_af.sequential_orchestrator import FOUNDRY_AGENT_NAME, PIPELINE_AGENTS

    endpoint = (
        os.getenv("AZURE_AI_PROJECT_ENDPOINT")
        or os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", "")
    )
    model = os.getenv("FOUNDRY_MODEL") or os.getenv(
        "AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4.1"
    )
    credential = DefaultAzureCredential()

    logger.info("  ⮞ INPUT: %d pipeline agents, endpoint=%s..., model=%s",
                 len(PIPELINE_AGENTS), endpoint[:40], model)

    # Register all pipeline agents in Foundry portal
    pc = AIProjectClient(endpoint=endpoint, credential=credential)

    for agent_name, agent_config in PIPELINE_AGENTS.items():
        try:
            existing = pc.agents.get(agent_name=agent_name)
            logger.info("  ✓ Agent '%s' already in Foundry Portal (version=%s)",
                         existing.name,
                         existing.versions.get("latest", {}).get("version", "unknown") if existing.versions else "unknown")
        except Exception:
            logger.info("  Agent '%s' not found — registering...", agent_name)
            agent = pc.agents.create_version(
                agent_name=agent_name,
                definition=PromptAgentDefinition(
                    model=model,
                    instructions=agent_config["instructions"],
                    temperature=0.0,
                ),
            )
            logger.info("  ✓ Agent '%s' registered (version=%s)",
                         agent.name, agent.version)

    # Verify all agents are visible in Foundry Portal
    for agent_name in PIPELINE_AGENTS:
        found = pc.agents.get(agent_name=agent_name)
        assert found is not None, f"Agent '{agent_name}' not visible in Foundry Portal"
    logger.info("  ✓ All %d pipeline agents VISIBLE in Foundry Portal", len(PIPELINE_AGENTS))

    # Also verify FoundryChatClient wraps the synthesis agent properly
    from agent_framework.foundry import FoundryChatClient

    logger.info("  Creating FoundryChatClient wrapper...")
    chat_client = FoundryChatClient(
        project_endpoint=endpoint,
        model=model,
        credential=credential,
    )
    af_agent = chat_client.as_agent(
        instructions="You are an HR policy assistant.",
        name=FOUNDRY_AGENT_NAME,
    )
    assert af_agent is not None

    logger.info("  ⮜ OUTPUT: %d agents in portal, AF wrapper created",
                 len(PIPELINE_AGENTS))
    logger.info("✓ Step 3 PASSED\n")


# =========================================================================
#  STEP 4: Run individual agents against live Azure
# =========================================================================

def step_4_test_individual_agents() -> dict[str, Any]:
    logger.info("=" * 70)
    logger.info("STEP 4: Test individual Agent Framework agents (LIVE)")
    logger.info("=" * 70)

    from agents_af.retrieval_agent import RetrievalAgent
    from agents_af.source_validator_agent import SourceValidatorAgent
    from agents_af.reference_validator_agent import ReferenceValidatorAgent
    from agents_af.answer_synthesis_agent import AnswerSynthesisAgent

    query = TEST_QUERIES[0]  # "Find Policy 51350 on Paid Time Off"
    qe = QUERY_EXPECTATIONS[0]
    logger.info("")
    logger.info("  ▶ QUERY: %s", query)
    logger.info("    expect: policy=%s  file=%s", qe.expected_policy, qe.expected_file[:50])
    logger.info("")

    # --- Retrieval Agent ---
    logger.info("  ─── 4a. RetrievalAgent (Agentic Retrieval) ───")
    logger.info("    ⮞ INPUT: query=%r", query)
    logger.info("    ⮞ AGENTIC RETRIEVAL COMPONENTS:")
    logger.info("      1. Query             → user query sent to Knowledge Base")
    logger.info("      2. Query Planning    → KB decomposes into sub-queries")
    logger.info("      3. Query Execution   → sub-queries run against index")
    logger.info("      4. Merged Results    → reranked matches from all sub-queries")
    logger.info("      5. Final Result      → synthesized answer + references")
    t0 = time.time()
    retrieval = RetrievalAgent()
    retrieval_output = retrieval.run(query)
    dt = time.time() - t0

    assert "matches" in retrieval_output
    assert len(retrieval_output["matches"]) > 0

    # --- Log Agentic Retrieval Components ---
    logger.info("")
    logger.info("    ⮜ AGENTIC RETRIEVAL OUTPUT (%.2fs):", dt)
    logger.info("      agentic_enabled: %s", retrieval._agentic_enabled)
    logger.info("")

    # Component 1: Query
    logger.info("      ┌─ [1] QUERY ─────────────────────────────────────────")
    logger.info("      │  user_query: %r", query)
    logger.info("      └────────────────────────────────────────────────────")

    # Component 2 & 3: Query Planning + Execution (from activity trace)
    activity = retrieval_output.get("activity", [])
    if activity:
        logger.info("      ┌─ [2] QUERY PLANNING (%d activity steps) ──────────", len(activity))
        for idx, step in enumerate(activity):
            step_type = step.get("type", "unknown") if isinstance(step, dict) else str(step)
            logger.info("      │  step[%d] type=%s", idx, step_type)

            # Log sub-queries from query planning
            if isinstance(step, dict):
                if "subqueries" in step:
                    logger.info("      │    sub-queries:")
                    for sq_idx, sq in enumerate(step["subqueries"]):
                        sq_text = sq.get("query", sq) if isinstance(sq, dict) else str(sq)
                        logger.info("      │      [%d] %s", sq_idx, sq_text)
                # searchIndex steps have search_index_arguments.search
                sia = step.get("search_index_arguments", {})
                if sia and sia.get("search"):
                    logger.info("      │    search: %s", sia["search"][:100])
                if "count" in step:
                    logger.info("      │    count: %s", step["count"])
                if "elapsed_ms" in step:
                    logger.info("      │    elapsed_ms: %s", step["elapsed_ms"])
                if "input_tokens" in step:
                    logger.info("      │    tokens: in=%s out=%s",
                                 step.get("input_tokens", "?"), step.get("output_tokens", "?"))
        logger.info("      └────────────────────────────────────────────────────")

        # Identify sub-query execution steps
        search_steps = [s for s in activity if isinstance(s, dict) and s.get("type") in ("searchIndex", "search", "vectorSearch")]
        if search_steps:
            logger.info("      ┌─ [3] QUERY EXECUTION (%d search operations) ─────", len(search_steps))
            for idx, ss in enumerate(search_steps):
                sia = ss.get("search_index_arguments", {})
                search_text = sia.get("search", "?") if sia else "?"
                logger.info("      │  search[%d]: type=%s  query=%s",
                             idx, ss.get("type", "?"),
                             str(search_text)[:80])
                if "count" in ss:
                    logger.info("      │    → %d results", ss["count"])
            logger.info("      └────────────────────────────────────────────────────")
    else:
        logger.info("      ┌─ [2] QUERY PLANNING ──────────────────────────────")
        logger.info("      │  (no activity trace — agentic retrieval may be disabled)")
        logger.info("      └────────────────────────────────────────────────────")

    # Component 4: Merged Results
    logger.info("      ┌─ [4] MERGED RESULTS (%d matches) ────────────────", len(retrieval_output["matches"]))
    for i, m in enumerate(retrieval_output["matches"]):
        score = m.get("reranker_score", "n/a")
        logger.info("      │  match[%d] policy=%-6s score=%-5s file=%s",
                     i, m.get("policyNumber", "?"), score,
                     m.get("fileName", "?")[:55])
    logger.info("      └────────────────────────────────────────────────────")

    # Component 5: Final Result (synthesized answer)
    logger.info("      ┌─ [5] FINAL RESULT ─────────────────────────────────")
    if "agentic_answer" in retrieval_output and retrieval_output["agentic_answer"]:
        logger.info("      │  agentic_answer: %s", retrieval_output["agentic_answer"][:200])
    else:
        logger.info("      │  agentic_answer: (none — extractive mode or fallback)")
    logger.info("      │  total_matches: %d", len(retrieval_output["matches"]))
    logger.info("      │  activity_steps: %d", len(activity))
    logger.info("      └────────────────────────────────────────────────────")

    # --- Source Validator ---
    logger.info("  ─── 4b. SourceValidatorAgent ───")
    logger.info("    ⮞ INPUT: %d matches from RetrievalAgent", len(retrieval_output["matches"]))
    t0 = time.time()
    val_output = SourceValidatorAgent().run(retrieval_output)
    dt = time.time() - t0

    assert val_output["source_count"] > 0
    logger.info("    ⮜ OUTPUT: %d/%d sources validated (trusted), %.2fs",
                 val_output["source_count"], len(retrieval_output["matches"]), dt)
    for s in val_output["validated_sources"][:3]:
        logger.info("      src: policy=%s  container=%s",
                     s.get("policyNumber", "?"), s.get("container", "?"))

    # --- Reference Validator ---
    logger.info("  ─── 4c. ReferenceValidatorAgent ───")
    logger.info("    ⮞ INPUT: %d validated sources from SourceValidatorAgent",
                 val_output["source_count"])
    t0 = time.time()
    ref_output = ReferenceValidatorAgent().run(val_output)
    dt = time.time() - t0

    assert ref_output["is_grounded"] is True
    assert len(ref_output["references"]) > 0
    logger.info("    ⮜ OUTPUT: grounded=%s, %d references, %.2fs",
                 ref_output["is_grounded"], len(ref_output["references"]), dt)
    for r in ref_output["references"][:3]:
        logger.info("      ref: policy=%s  file=%s",
                     r.get("policyNumber", "?"), r.get("fileName", "?")[:60])

    # --- Answer Synthesis (deterministic check) ---
    logger.info("  ─── 4d. AnswerSynthesisAgent ───")
    synth = AnswerSynthesisAgent()
    agentic_answer = retrieval_output.get("agentic_answer")
    logger.info("    ⮞ INPUT: query=%r, %d refs, grounded=%s, has_agentic=%s",
                 query[:40], len(ref_output["references"]),
                 ref_output["is_grounded"], bool(agentic_answer))

    if agentic_answer:
        result = synth.run(
            user_query=query,
            validated_sources=val_output["validated_sources"],
            references=ref_output["references"],
            is_grounded=True,
            agentic_answer=agentic_answer,
        )
        assert result["source"] == "agentic_retrieval"
        logger.info("    ⮜ OUTPUT: source=agentic_retrieval (passthrough)")
        logger.info("      answer: %s...", result["answer"][:100])
    else:
        result = synth.run(
            user_query=query,
            validated_sources=val_output["validated_sources"],
            references=ref_output["references"],
            is_grounded=True,
        )
        logger.info("    ⮜ OUTPUT: source=%s", result["source"])
        logger.info("      answer: %s...", result["answer"][:100])

    logger.info("")
    logger.info("✓ Step 4 PASSED\n")
    return {"retrieval": retrieval_output, "validation": val_output, "reference": ref_output}


# =========================================================================
#  STEP 5: Full orchestrator pipeline – all queries
# =========================================================================

def step_5_full_orchestrator() -> list[dict]:
    logger.info("=" * 70)
    logger.info("STEP 5: Full AF Orchestrator – %d queries (LIVE)", len(TEST_QUERIES))
    logger.info("=" * 70)

    from agents_af.sequential_orchestrator import SequentialWorkflowOrchestrator

    orch = SequentialWorkflowOrchestrator()
    logger.info("  project_endpoint : %s",
                 orch.project_endpoint[:45] + "..." if orch.project_endpoint else "(not set)")
    logger.info("  model            : %s", orch.model)
    logger.info("  pipeline         : Retrieval → SourceValidation → RefValidation → "
                 "AnswerSynthesis → FoundryChatClient → FinalSynthesis")
    logger.info("")

    results = []
    passed = 0
    failed = 0

    for i, query in enumerate(TEST_QUERIES, 1):
        qe = QUERY_EXPECTATIONS[i - 1]

        logger.info("  ┌──────────────────────────────────────────────────────────────")
        logger.info("  ▶ QUERY %d/%d: %s", i, len(TEST_QUERIES), query)
        logger.info("    expect: policy=%s  desc=%s", qe.expected_policy, qe.description)
        logger.info("  └──────────────────────────────────────────────────────────────")

        logger.info("    ⮞ INPUT: query=%r", query)

        t0 = time.time()
        try:
            result = orch.process_query(query)
            dt = time.time() - t0

            assert result["status"] == "completed", f"Status: {result['status']}"
            assert result["is_grounded"] is True, "Not grounded"
            assert len(result["matches"]) > 0, "No matches"
            assert len(result["references"]) > 0, "No references"
            assert result["answer"], "Empty answer"

            logger.info("    ⮜ OUTPUT (%.2fs):", dt)
            logger.info("      status     : %s", result["status"])
            logger.info("      grounded   : %s", result["is_grounded"])
            logger.info("")

            # --- Agentic Retrieval Components for this query ---
            logger.info("      ┌─ AGENTIC RETRIEVAL TRACE ───────────────────────")

            # [1] Query
            logger.info("      │ [1] Query: %s", query)

            # [2] Query Planning (from steps metadata if available)
            steps_data = result.get("steps", {})
            retrieval_step = steps_data.get("retrieval", {})
            logger.info("      │ [2] Query Planning: match_count=%s",
                         retrieval_step.get("match_count", "?"))

            # [3] Query Execution — sub-queries (from activity if passed through)
            # Note: Activity trace is available in Step 4 (direct RetrievalAgent call).
            # The AF orchestrator does not expose activity in its final output dict;
            # sub-query details are logged internally by the RetrievalExecutor.
            activity = result.get("activity", [])
            if activity:
                search_ops = [a for a in activity if isinstance(a, dict) and a.get("type") in ("searchIndex", "search", "vectorSearch")]
                planning_ops = [a for a in activity if isinstance(a, dict) and a.get("type") in ("modelQueryPlanning", "queryPlanning")]
                if planning_ops:
                    for p in planning_ops:
                        sqs = p.get("subqueries", [])
                        if sqs:
                            logger.info("      │ [3] Sub-queries (%d):", len(sqs))
                            for sq_i, sq in enumerate(sqs):
                                sq_text = sq.get("query", sq) if isinstance(sq, dict) else str(sq)
                                logger.info("      │       [%d] %s", sq_i, sq_text[:80])
                if search_ops:
                    logger.info("      │     Search executions: %d", len(search_ops))
                    for s_i, s_op in enumerate(search_ops):
                        sia = s_op.get("search_index_arguments", {})
                        search_text = sia.get("search", "?") if sia else "?"
                        logger.info("      │       exec[%d] type=%s  query=%s  count=%s",
                                     s_i, s_op.get("type", "?"),
                                     str(search_text)[:60],
                                     s_op.get("count", "?"))
            else:
                logger.info("      │ [3] Sub-queries: (activity not in pipeline output)")

            # [4] Merged Results
            logger.info("      │ [4] Merged Results: %d matches, %d validated, %d refs",
                         len(result.get("matches", [])),
                         len(result.get("validated_sources", [])),
                         len(result.get("references", [])))
            for j, m in enumerate(result["matches"][:3]):
                logger.info("      │       match[%d] policy=%-6s score=%-5s file=%s",
                             j, m.get("policyNumber", "?"),
                             m.get("reranker_score", "n/a"),
                             m.get("fileName", "?")[:50])

            # [5] Final Result
            logger.info("      │ [5] Final Result:")
            logger.info("      │       answer: %s...", result["answer"][:120])
            logger.info("      └─────────────────────────────────────────────────")

            # Check expected policy
            if qe.expected_policy:
                policies_found = [
                    m.get("policyNumber", "") for m in result["matches"]
                ]
                if qe.expected_policy in str(policies_found):
                    logger.info("    ✓ expected policy %s FOUND", qe.expected_policy)
                else:
                    logger.warning("    ⚠ expected policy %s NOT in top results: %s",
                                    qe.expected_policy, policies_found[:5])

            results.append({"query": query, "status": "passed", "result": result})
            passed += 1

        except Exception as e:
            dt = time.time() - t0
            logger.error("    ✗ FAILED: %s (%.2fs)", e, dt)
            results.append({"query": query, "status": "failed", "error": str(e)})
            failed += 1

        logger.info("")

    logger.info("  ┌──────────────────────────────────────────────────────────────")
    logger.info("  SUMMARY: %d/%d passed, %d failed", passed, len(TEST_QUERIES), failed)
    logger.info("  └──────────────────────────────────────────────────────────────")
    logger.info("✓ Step 5 %s\n", "PASSED" if failed == 0 else "FAILED")

    return results


# =========================================================================
#  Main
# =========================================================================

def main():
    logger.info("╔" + "═" * 68 + "╗")
    logger.info("║  Live E2E – Agent Framework + FoundryChatClient (agents_af/)      ║")
    logger.info("║  Mode: LIVE (no mocks)                                            ║")
    logger.info("║  Queries: %d from test_queries.py                                 ║",
                 len(TEST_QUERIES))
    logger.info("╚" + "═" * 68 + "╝")
    logger.info("")

    total_start = time.time()
    passed = 0
    failed = 0
    total = 5

    steps = [
        ("Step 1: Verify environment", step_1_verify_environment),
        ("Step 2: Verify AF imports", step_2_verify_imports),
        ("Step 3: Agent registration in Foundry Portal", step_3_verify_agent_registration),
        ("Step 4: Individual agents (LIVE)", step_4_test_individual_agents),
        ("Step 5: Full orchestrator (LIVE)", step_5_full_orchestrator),
    ]

    for name, fn in steps:
        try:
            fn()
            passed += 1
        except Exception as e:
            logger.error("✗ %s FAILED: %s", name, e)
            failed += 1

    total_time = time.time() - total_start

    logger.info("=" * 70)
    logger.info("FINAL RESULTS")
    logger.info("=" * 70)
    logger.info("  Steps   : %d/%d passed, %d failed", passed, total, failed)
    logger.info("  Queries : %d total", len(TEST_QUERIES))
    logger.info("  Elapsed : %.1fs", total_time)
    logger.info("=" * 70)

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
