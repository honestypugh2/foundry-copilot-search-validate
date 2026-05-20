#!/usr/bin/env python3
"""
Live End-to-End – MCP Query Retrieval (single-agent MCP)

NO mocks. Connects to live Azure AI Search and Azure AI Foundry.
Every query runs the full FoundryAgentOrchestrator single-agent MCP pipeline:

    Azure AI Search (Agentic Retrieval)
        → FoundryAgentOrchestrator    (single-agent MCP)
        → Citation validation         (MCP annotation check)

Validates that the answer references the expected policy number and/or
filename for each canonical test query.

Usage:
    cd foundry-copilot-search-validate
    PYTHONPATH=$PWD/src python tests/test_mcp_query_retrieval.py

    # Run a subset (first N queries)
    PYTHONPATH=$PWD/src python tests/test_mcp_query_retrieval.py --limit 3

Environment variables required (from .env):
    AZURE_SEARCH_ENDPOINT
    AZURE_AI_PROJECT_ENDPOINT
"""

import argparse
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
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
_log_file = _log_dir / "test_mcp_query_retrieval.log"

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

logger = logging.getLogger("test_mcp_query_retrieval")
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
from test_queries import QUERY_EXPECTATIONS, QueryExpectation


# =========================================================================
#  Data classes
# =========================================================================

@dataclass
class MCPTestResult:
    query: str
    expected_policy: str
    expected_file: str
    description: str
    answer: str = ""
    passed: bool = False
    policy_found: bool = False
    file_found: bool = False
    has_citations: bool = False
    citation_count: int = 0
    token_usage: dict = field(default_factory=dict)
    activity: list = field(default_factory=list)
    error: str = ""
    elapsed: float = 0.0
    root_causes: list[str] = field(default_factory=list)


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

    model = (
        os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME")
        or os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
        or os.getenv("FOUNDRY_MODEL", "gpt-4.1")
    )
    logger.info("  ✓ MODEL = %s", model)

    from azure.identity import DefaultAzureCredential
    token = DefaultAzureCredential().get_token(
        "https://cognitiveservices.azure.com/.default"
    )
    assert token.token, "Failed to acquire Azure token"
    logger.info("  ✓ DefaultAzureCredential acquired token")

    logger.info("  ⮜ OUTPUT: Azure endpoint=%s, model=%s, credential=OK",
                 required_vars["AZURE_SEARCH_ENDPOINT"][:30] + "...", model)
    logger.info("✓ Step 1 PASSED\n")
    return required_vars


# =========================================================================
#  STEP 2: Verify SDK imports (FoundryAgentOrchestrator + MCP)
# =========================================================================

def step_2_verify_imports() -> None:
    logger.info("=" * 70)
    logger.info("STEP 2: Verify FoundryAgentOrchestrator + MCP imports")
    logger.info("=" * 70)

    logger.info("  ⮞ INPUT: import azure.ai.projects, agents/ modules")

    from azure.ai.projects import AIProjectClient
    logger.info("  ✓ azure.ai.projects: AIProjectClient")

    from agents.sequential_orchestrator_foundry import FoundryAgentOrchestrator
    logger.info("  ✓ agents.sequential_orchestrator_foundry: FoundryAgentOrchestrator")

    from agents.retrieval_agent import RetrievalAgent
    logger.info("  ✓ agents.retrieval_agent: RetrievalAgent")

    from agents.source_validator_agent import SourceValidatorAgent
    logger.info("  ✓ agents.source_validator_agent: SourceValidatorAgent")

    from agents.reference_validator_agent import ReferenceValidatorAgent
    logger.info("  ✓ agents.reference_validator_agent: ReferenceValidatorAgent")

    from agents.answer_synthesis_agent import AnswerSynthesisAgent
    logger.info("  ✓ agents.answer_synthesis_agent: AnswerSynthesisAgent")

    logger.info("  ⮜ OUTPUT: 6 modules imported successfully")
    logger.info("✓ Step 2 PASSED\n")


# =========================================================================
#  STEP 3: Initialize orchestrator
# =========================================================================

def step_3_initialize_orchestrator(pattern: str = "A"):
    logger.info("=" * 70)
    logger.info("STEP 3: Initialize Orchestrator (Pattern %s)", pattern)
    logger.info("=" * 70)

    from agents.sequential_orchestrator_foundry import OUTPUT_MODE

    logger.info("  ⮞ INPUT: VALIDATE_CITATIONS=true, OUTPUT_MODE=%s, PATTERN=%s", OUTPUT_MODE, pattern)

    os.environ["VALIDATE_CITATIONS"] = "true"
    os.environ["ORCHESTRATOR_PATTERN"] = pattern

    from agents.orchestrator_factory import get_orchestrator

    t0 = time.time()
    orchestrator = get_orchestrator()
    orchestrator._validate_citations = True
    dt = time.time() - t0

    logger.info("  ✓ Orchestrator instantiated (%.2fs)", dt)
    logger.info("  ✓ Pattern: %s", pattern)
    logger.info("  ✓ Output mode: %s", orchestrator._output_mode)
    logger.info("  ✓ Citation validation enabled")
    if orchestrator._output_mode == "EXTRACTIVE":
        logger.info("  ✓ Extractive mode: answer_instructions NOT used")
    else:
        logger.info("  ✓ Answer synthesis mode: answer_instructions active")
    logger.info("  ⮜ OUTPUT: orchestrator ready, pattern=%s, output_mode=%s",
                 pattern, orchestrator._output_mode)
    logger.info("✓ Step 3 PASSED\n")
    return orchestrator


# =========================================================================
#  STEP 4: Run MCP queries – all test cases
# =========================================================================

def step_4_run_queries(orchestrator, limit: int | None = None, query_id: int | None = None) -> list[MCPTestResult]:
    if query_id is not None:
        expectations = [qe for qe in QUERY_EXPECTATIONS if qe.query_id == query_id]
    elif limit:
        expectations = QUERY_EXPECTATIONS[:limit]
    else:
        expectations = QUERY_EXPECTATIONS

    logger.info("=" * 70)
    logger.info("STEP 4: MCP Query Retrieval – %d queries (LIVE)", len(expectations))
    logger.info("=" * 70)

    logger.info("  pipeline     : FoundryAgentOrchestrator (single-agent MCP)")
    logger.info("  output_mode  : %s", orchestrator._output_mode)
    logger.info("    → Azure AI Search (Agentic Retrieval)")
    logger.info("    → MCP tool invocation")
    if orchestrator._output_mode == "EXTRACTIVE":
        logger.info("    → Extractive passages (no synthesis)")
    else:
        logger.info("    → Answer synthesis")
    logger.info("    → Citation validation")
    logger.info("")

    results: list[MCPTestResult] = []
    passed = 0
    failed = 0

    for i, qe in enumerate(expectations, 1):
        logger.info("  ┌──────────────────────────────────────────────────────────────")
        logger.info("  ▶ QUERY %d/%d: %s", i, len(expectations), qe.query)
        logger.info("    expect: policy=%s  file=%s  desc=%s",
                     qe.expected_policy or "(any)", qe.expected_file[:50], qe.description)
        logger.info("  └──────────────────────────────────────────────────────────────")

        logger.info("    ⮞ INPUT: query=%r", qe.query)

        t0 = time.time()
        try:
            result = orchestrator.process_query(qe.query)
            dt = time.time() - t0

            tr = _evaluate_result(qe, result)
            tr.elapsed = dt
            tr.token_usage = result.get("token_usage", {})
            tr.activity = result.get("activity", [])

            logger.info("    ⮜ OUTPUT:")
            logger.info("      status     : %s", result.get("status", "unknown"))
            logger.info("      output_mode: %s", result.get("output_mode", "unknown"))
            logger.info("      answer     : %s...", tr.answer[:120] if tr.answer else "(empty)")
            logger.info("      elapsed    : %.2fs", dt)

            # Log citation details
            citation_validation = result.get("citation_validation", {})
            logger.info("      citations  : %d", tr.citation_count)
            if citation_validation:
                logger.info("      cite_valid : has_citations=%s, count=%d",
                             citation_validation.get("has_citations", False),
                             citation_validation.get("citation_count", 0))

            # Log token usage
            token_usage = result.get("token_usage", {})
            if token_usage and any(v > 0 for v in token_usage.values()):
                logger.info("      ┌─ Token Usage ─────────────────────────────────")
                logger.info("      │ Prompt: %d │ Output: %d │ Total: %d",
                             token_usage.get("prompt_tokens", 0),
                             token_usage.get("completion_tokens", 0),
                             token_usage.get("total_tokens", 0))
                logger.info("      └───────────────────────────────────────────────")

            # Log subqueries/activity
            activity = result.get("activity", [])
            if activity:
                logger.info("      ┌─ Subqueries ──────────────────────────────────")
                logger.info("      │ %-40s │ %6s │ %10s", "Subquery", "Count", "Elapsed MS")
                logger.info("      │ %s", "─" * 62)
                for act in activity:
                    search_args = act.get("searchIndexArguments", {})
                    subquery_text = search_args.get("search", act.get("query", ""))
                    count = act.get("count", 0)
                    elapsed_ms = act.get("elapsedMs", 0)
                    if subquery_text:
                        display_q = (subquery_text[:37] + "...") if len(subquery_text) > 40 else subquery_text
                        logger.info("      │ %-40s │ %6d │ %10d", display_q, count, elapsed_ms)
                logger.info("      └───────────────────────────────────────────────")

            # Log policy/file match details
            if tr.policy_found:
                logger.info("    ✓ expected policy %s FOUND in answer", qe.expected_policy)
            elif qe.expected_policy:
                logger.warning("    ⚠ expected policy %s NOT in answer", qe.expected_policy)

            if tr.file_found:
                logger.info("    ✓ expected file reference FOUND in answer")
            else:
                logger.warning("    ⚠ expected file '%s' NOT in answer", qe.expected_file[:50])

            if tr.has_citations:
                logger.info("    ✓ MCP citations present (%d)", tr.citation_count)
            else:
                logger.warning("    ⚠ NO MCP citation annotations in answer")

            if tr.passed:
                logger.info("    ✓ PASSED")
                passed += 1
            else:
                logger.error("    ✗ FAILED")
                failed += 1
                for rc in tr.root_causes:
                    logger.warning("      ⚠ ROOT CAUSE: %s", rc)

        except Exception as e:
            dt = time.time() - t0
            logger.error("    ✗ EXCEPTION: %s (%.2fs)", e, dt)
            tr = MCPTestResult(
                query=qe.query,
                expected_policy=qe.expected_policy,
                expected_file=qe.expected_file,
                description=qe.description,
                error=str(e),
                elapsed=dt,
            )
            tr.root_causes.append(f"EXCEPTION: {e}")
            failed += 1

        results.append(tr)
        logger.info("")

    logger.info("  ┌──────────────────────────────────────────────────────────────")
    logger.info("  SUMMARY: %d/%d passed, %d failed", passed, len(expectations), failed)
    logger.info("  └──────────────────────────────────────────────────────────────")
    logger.info("✓ Step 4 %s\n", "PASSED" if failed == 0 else "FAILED")

    return results


# =========================================================================
#  STEP 5: Write results and final summary
# =========================================================================

def step_5_write_results(results: list[MCPTestResult]) -> None:
    logger.info("=" * 70)
    logger.info("STEP 5: Write results + final summary")
    logger.info("=" * 70)

    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed

    logger.info("  ⮞ INPUT: %d test results", len(results))

    # Write JSON results
    json_path = Path(__file__).resolve().parent / "mcp_query_retrieval_results.json"
    records = []
    for tr in results:
        records.append({
            "query": tr.query,
            "description": tr.description,
            "expected_policy": tr.expected_policy,
            "expected_file": tr.expected_file,
            "passed": tr.passed,
            "policy_found": tr.policy_found,
            "file_found": tr.file_found,
            "has_citations": tr.has_citations,
            "citation_count": tr.citation_count,
            "answer": tr.answer,
            "root_causes": tr.root_causes,
            "error": tr.error,
            "elapsed_seconds": round(tr.elapsed, 2),
            "token_usage": tr.token_usage,
            "activity": tr.activity,
        })
    with open(json_path, "w") as f:
        json.dump(records, f, indent=2)
    logger.info("  ✓ Results written to %s", json_path)

    # Root cause summary
    if failed > 0:
        logger.info("")
        logger.info("  ROOT CAUSE SUMMARY:")
        cause_counts: dict[str, int] = {}
        for tr in results:
            if not tr.passed:
                for rc in tr.root_causes:
                    tag = rc.split(":")[0]
                    cause_counts[tag] = cause_counts.get(tag, 0) + 1
        for tag, count in sorted(cause_counts.items(), key=lambda x: -x[1]):
            logger.info("    ⚠ %s: %d occurrence(s)", tag, count)

    logger.info("")
    logger.info("  ⮜ OUTPUT: %d/%d passed, %d failed", passed, len(results), failed)
    logger.info("✓ Step 5 PASSED\n")


# =========================================================================
#  Evaluation helper
# =========================================================================

def _evaluate_result(qe: QueryExpectation, result: dict) -> MCPTestResult:
    tr = MCPTestResult(
        query=qe.query,
        expected_policy=qe.expected_policy,
        expected_file=qe.expected_file,
        description=qe.description,
    )

    if "error" in result and result.get("status") != "completed":
        tr.error = str(result.get("error", "unknown error"))
        tr.root_causes.append(f"PIPELINE_ERROR: {tr.error}")
        return tr

    answer = result.get("answer", "")
    tr.answer = answer

    # Check if expected policy number appears in the answer
    if qe.expected_policy:
        tr.policy_found = qe.expected_policy in answer
        if not tr.policy_found:
            tr.root_causes.append(
                f"POLICY_MISSING: Expected '{qe.expected_policy}' not in answer"
            )
    else:
        tr.policy_found = True  # No policy expected (e.g., SOP docs)

    # Check if expected filename (or key parts) appears in the answer
    file_stem = qe.expected_file.rsplit(".", 1)[0] if "." in qe.expected_file else qe.expected_file
    # Normalize for comparison: underscores → spaces, case-insensitive
    answer_lower = answer.lower()
    file_stem_normalized = file_stem.replace("_", " ").lower()
    # Extract title portion (after policy number prefix "XXXXX - ")
    title_portion = ""
    if " - " in qe.expected_file:
        title_portion = qe.expected_file.split(" - ", 1)[1].split("(")[0].strip()
    title_normalized = title_portion.replace("_", " ").lower()
    # Extract the core topic (strip leading category like "Hiring_ ")
    core_topic = title_normalized
    if "_ " in title_portion:
        core_topic = title_portion.split("_ ", 1)[1].split("(")[0].strip().lower()

    tr.file_found = (
        qe.expected_file in answer
        or file_stem in answer
        or file_stem_normalized in answer_lower
        or (bool(title_portion) and title_portion in answer)
        or (bool(title_normalized) and title_normalized in answer_lower)
        or (len(core_topic) > 3 and core_topic in answer_lower)
    )
    if not tr.file_found:
        tr.root_causes.append(
            f"FILE_MISSING: Expected reference to '{qe.expected_file}' not in answer"
        )

    # Check citation annotations
    citation_validation = result.get("citation_validation", {})
    tr.has_citations = citation_validation.get("has_citations", False)
    tr.citation_count = citation_validation.get("citation_count", 0)

    # Also check for citation pattern directly in answer if validation wasn't run
    if not tr.has_citations:
        citations = re.findall(r"【\d+:\d+†[^】]+】", answer)
        tr.has_citations = len(citations) > 0
        tr.citation_count = len(citations)

    if not tr.has_citations:
        tr.root_causes.append("NO_CITATIONS: No MCP citation annotations in answer")

    tr.passed = tr.policy_found and tr.file_found
    return tr


# =========================================================================
#  Main
# =========================================================================

def main():
    parser = argparse.ArgumentParser(
        description="MCP query retrieval test — FoundryAgentOrchestrator (single-agent MCP)"
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Run only the first N queries",
    )
    parser.add_argument(
        "--query_id", type=int, default=None,
        help="Run a single query by its 1-based position in QUERY_EXPECTATIONS",
    )
    parser.add_argument(
        "--pattern", type=str, default="A", choices=["A", "B"],
        help="Orchestrator pattern: A (single-agent MCP) or B (hybrid MCP + metadata)",
    )
    args = parser.parse_args()

    from agents.sequential_orchestrator_foundry import OUTPUT_MODE

    if args.query_id is not None:
        matches = [qe for qe in QUERY_EXPECTATIONS if qe.query_id == args.query_id]
        if not matches:
            logger.error("--query_id=%d not found. Valid IDs: 1-%d",
                         args.query_id, max(qe.query_id for qe in QUERY_EXPECTATIONS))
            sys.exit(1)
        n = 1
    else:
        n = args.limit or len(QUERY_EXPECTATIONS)

    logger.info("╔" + "═" * 68 + "╗")
    logger.info("║  Live E2E – MCP Query Retrieval (Pattern %s)                       ║", args.pattern)
    logger.info("║  Mode: LIVE (no mocks)                                            ║")
    logger.info("║  Output Mode: %-53s║", OUTPUT_MODE)
    logger.info("║  Queries: %d from test_queries.py                                 ║", n)
    logger.info("║  Pipeline: Orchestrator (Pattern %s) → MCP → Citations             ║", args.pattern)
    logger.info("╚" + "═" * 68 + "╝")
    logger.info("")

    total_start = time.time()
    passed_steps = 0
    failed_steps = 0
    total_steps = 5
    results: list[MCPTestResult] = []

    # Step 1: Verify environment
    try:
        step_1_verify_environment()
        passed_steps += 1
    except Exception as e:
        logger.error("✗ Step 1 FAILED: %s", e)
        failed_steps += 1
        sys.exit(1)

    # Step 2: Verify imports
    try:
        step_2_verify_imports()
        passed_steps += 1
    except Exception as e:
        logger.error("✗ Step 2 FAILED: %s", e)
        failed_steps += 1
        sys.exit(1)

    # Step 3: Initialize orchestrator
    try:
        orchestrator = step_3_initialize_orchestrator(pattern=args.pattern)
        passed_steps += 1
    except Exception as e:
        logger.error("✗ Step 3 FAILED: %s", e)
        failed_steps += 1
        sys.exit(1)

    # Step 4: Run queries
    try:
        results = step_4_run_queries(orchestrator, limit=args.limit, query_id=args.query_id)
        passed_steps += 1
    except Exception as e:
        logger.error("✗ Step 4 FAILED: %s", e)
        failed_steps += 1

    # Step 5: Write results
    try:
        step_5_write_results(results)
        passed_steps += 1
    except Exception as e:
        logger.error("✗ Step 5 FAILED: %s", e)
        failed_steps += 1

    total_time = time.time() - total_start

    # Final results
    query_passed = sum(1 for r in results if r.passed)
    query_failed = len(results) - query_passed

    logger.info("=" * 70)
    logger.info("FINAL RESULTS")
    logger.info("=" * 70)
    logger.info("  Steps   : %d/%d passed, %d failed", passed_steps, total_steps, failed_steps)
    logger.info("  Queries : %d/%d passed, %d failed", query_passed, len(results), query_failed)
    logger.info("  Elapsed : %.1fs", total_time)
    logger.info("=" * 70)

    sys.exit(0 if query_failed == 0 else 1)


if __name__ == "__main__":
    main()
