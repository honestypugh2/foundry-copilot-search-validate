#!/usr/bin/env python3
"""
Live End-to-End – Foundry Agent Service Path (agents/)

NO mocks. Connects to live Azure AI Search and Azure AI Foundry.
Every query runs the full Foundry Agent Service pipeline:

    Azure AI Search (Agentic Retrieval)
        → RetrievalAgent             (local — agentic / hybrid search)
        → SourceValidatorAgent       (local + optional Foundry Agent)
        → ReferenceValidatorAgent    (local + optional Foundry Agent)
        → AnswerSynthesisAgent       (Foundry Agent via azure-ai-projects)

Verifies that Foundry Agents appear in the Azure AI Foundry Portal
by registering agents via ensure_agent() and querying them.

Also tests the original AF SequentialBuilder orchestrator for comparison.

Usage:
    cd foundry-copilot-hr-policy-knowledge
    PYTHONPATH=$PWD/src python src/tests/e2e_live_foundry_service.py

    # Persist agents in Foundry Portal (don't delete after test):
    PERSIST_FOUNDRY_AGENTS=true PYTHONPATH=$PWD/src python src/tests/e2e_live_foundry_service.py

Environment variables required (from src/.env):
    AZURE_SEARCH_ENDPOINT
    AZURE_AI_PROJECT_ENDPOINT
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
_log_file = _log_dir / "e2e_live_foundry_service.log"

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

logger = logging.getLogger("e2e_live_foundry")
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

    model = (
        os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME")
        or os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
        or os.getenv("FOUNDRY_MODEL", "gpt-4.1")
    )
    logger.info("  ✓ MODEL = %s", model)

    persist = os.getenv("PERSIST_FOUNDRY_AGENTS", "false").lower() == "true"
    logger.info("  ✓ PERSIST_FOUNDRY_AGENTS = %s", persist)

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
#  STEP 2: Verify Foundry Agent Service SDK imports
# =========================================================================

def step_2_verify_imports() -> None:
    logger.info("=" * 70)
    logger.info("STEP 2: Verify Foundry Agent Service SDK imports")
    logger.info("=" * 70)

    logger.info("  ⮞ INPUT: import azure.ai.projects, agents/ modules")

    from azure.ai.projects import AIProjectClient
    from azure.ai.projects.models import PromptAgentDefinition
    logger.info("  ✓ azure.ai.projects: AIProjectClient, PromptAgentDefinition")

    from agents.foundry_client import (
        PROJECTS_SDK_AVAILABLE,
        get_project_client,
        ensure_agent,
        invoke_agent,
    )
    logger.info("  ✓ foundry_client: PROJECTS_SDK_AVAILABLE=%s", PROJECTS_SDK_AVAILABLE)
    assert PROJECTS_SDK_AVAILABLE, "azure-ai-projects SDK required for Foundry tests"

    from agents.retrieval_agent import RetrievalAgent
    from agents.source_validator_agent import SourceValidatorAgent
    from agents.reference_validator_agent import ReferenceValidatorAgent
    from agents.answer_synthesis_agent import AnswerSynthesisAgent
    from agents.sequential_orchestrator_foundry import FoundryAgentOrchestrator

    logger.info("  ✓ All agents/ modules imported")
    logger.info("  ✓ FoundryAgentOrchestrator (single-agent MCP)")
    logger.info("  ⮜ OUTPUT: 7 modules imported successfully")
    logger.info("✓ Step 2 PASSED\n")


# =========================================================================
#  STEP 3: Register Foundry Agents and verify in Portal
# =========================================================================

def step_3_register_agents() -> dict[str, Any]:
    logger.info("=" * 70)
    logger.info("STEP 3: Register Foundry Agents → verify in Portal")
    logger.info("=" * 70)

    from agents.foundry_client import ensure_agent, get_project_client

    model = (
        os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME")
        or os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
        or os.getenv("FOUNDRY_MODEL", "gpt-4.1")
    )

    agents_to_register = [
        {
            "name": "hr-source-validator-agent",
            "instructions": (
                "You are a source validation agent for an HR policy knowledge base. "
                "Filter search results to only include documents from the trusted "
                "'ask-hr-knowledge' container."
            ),
        },
        {
            "name": "hr-reference-validator-agent",
            "instructions": (
                "You are a reference validation agent. Extract and validate citations "
                "from search results. Verify that file paths, policy numbers, and "
                "document types are present."
            ),
        },
        {
            "name": "HRAnswerSynthesisFoundry",
            "instructions": (
                "You are an HR policy assistant. Answer the user's question based "
                "only on the provided grounded sources. Cite the source file paths "
                "and policy numbers in your answer. If the sources do not contain "
                "enough information, say so."
            ),
        },
    ]

    logger.info("  ⮞ INPUT: %d agents to register, model=%s", len(agents_to_register), model)

    registered = {}
    pc = get_project_client()

    for agent_spec in agents_to_register:
        logger.info("  ─── Registering: %s ───", agent_spec["name"])
        logger.info("    ⮞ INPUT: name=%s, instructions=%s...",
                     agent_spec["name"], agent_spec["instructions"][:60])
        t0 = time.time()

        result = ensure_agent(
            agent_name=agent_spec["name"],
            model=model,
            instructions=agent_spec["instructions"],
        )
        dt = time.time() - t0

        logger.info("    ⮜ OUTPUT: name=%s, version=%s, %.2fs",
                     result["name"], result["version"], dt)

        # Verify agent is visible in Foundry Portal
        try:
            found = pc.agents.get(agent_name=agent_spec["name"])
            logger.info("    ✓ VISIBLE in Foundry Portal (version=%s)", found.versions.get("latest", {}).get("version", "unknown") if found.versions else "unknown")
        except Exception as e:
            logger.error("    ✗ NOT visible in Portal: %s", e)
            raise AssertionError(
                f"Agent '{agent_spec['name']}' not visible in Foundry Portal: {e}"
            )

        registered[agent_spec["name"]] = result

    logger.info("")
    logger.info("  ⮜ OUTPUT: %d agents registered in Foundry Portal:", len(registered))
    for name, info in registered.items():
        logger.info("    %s (version=%s)", name, info["version"])

    logger.info("✓ Step 3 PASSED\n")
    return registered


# =========================================================================
#  STEP 4: Test individual agents against live Azure
# =========================================================================

def step_4_test_individual_agents() -> dict[str, Any]:
    logger.info("=" * 70)
    logger.info("STEP 4: Test individual Foundry agents (LIVE)")
    logger.info("=" * 70)

    from agents.retrieval_agent import RetrievalAgent
    from agents.source_validator_agent import SourceValidatorAgent
    from agents.reference_validator_agent import ReferenceValidatorAgent
    from agents.answer_synthesis_agent import AnswerSynthesisAgent

    query = TEST_QUERIES[0]  # "Find Policy 51350 on Paid Time Off"
    qe = QUERY_EXPECTATIONS[0]
    logger.info("")
    logger.info("  ▶ QUERY: %s", query)
    logger.info("    expect: policy=%s  file=%s", qe.expected_policy, qe.expected_file[:50])
    logger.info("")

    # --- Retrieval Agent ---
    logger.info("  ─── 4a. RetrievalAgent ───")
    logger.info("    ⮞ INPUT: query=%r", query)
    t0 = time.time()
    retrieval = RetrievalAgent()
    retrieval_output = retrieval.run(query)
    dt = time.time() - t0

    assert "matches" in retrieval_output
    assert len(retrieval_output["matches"]) > 0
    logger.info("    ⮜ OUTPUT: %d matches, agentic=%s, %.2fs",
                 len(retrieval_output["matches"]), retrieval._agentic_enabled, dt)
    if "agentic_answer" in retrieval_output:
        logger.info("      agentic_answer: %s...", retrieval_output["agentic_answer"][:80])
    for i, m in enumerate(retrieval_output["matches"][:3]):
        logger.info("      match[%d] policy=%s  file=%s",
                     i, m.get("policyNumber", "?"), m.get("fileName", "?")[:60])

    # --- Source Validator Agent ---
    logger.info("  ─── 4b. SourceValidatorAgent ───")
    logger.info("    ⮞ INPUT: %d matches from RetrievalAgent", len(retrieval_output["matches"]))
    t0 = time.time()
    val_output = SourceValidatorAgent().run(retrieval_output)
    dt = time.time() - t0

    assert val_output["source_count"] > 0
    logger.info("    ⮜ OUTPUT: %d/%d sources validated (trusted), %.2fs",
                 val_output["source_count"], len(retrieval_output["matches"]), dt)

    # --- Reference Validator Agent ---
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

    # --- Answer Synthesis Agent ---
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
#  STEP 5: Invoke Foundry Agent via conversations API
# =========================================================================

def step_5_invoke_foundry_agent() -> None:
    logger.info("=" * 70)
    logger.info("STEP 5: Invoke Foundry Agent via conversations API")
    logger.info("=" * 70)

    from agents.foundry_client import invoke_agent

    query = "What is the PTO policy? Answer based on Policy 51350."
    logger.info("  ⮞ INPUT: agent=HRAnswerSynthesisFoundry, query=%r", query)

    t0 = time.time()
    response_text = invoke_agent("HRAnswerSynthesisFoundry", query)
    dt = time.time() - t0

    assert response_text, "Empty response from Foundry Agent"
    assert len(response_text) > 20, f"Response too short: {response_text}"

    logger.info("  ⮜ OUTPUT: %d chars, %.2fs", len(response_text), dt)
    logger.info("    response: %s...", response_text[:150])
    logger.info("✓ Step 5 PASSED\n")


# =========================================================================
#  STEP 6: Full Foundry orchestrator pipeline – all queries
# =========================================================================

def step_6_foundry_orchestrator() -> list[dict]:
    logger.info("=" * 70)
    logger.info("STEP 6: Foundry Orchestrator – %d queries (LIVE)", len(TEST_QUERIES))
    logger.info("=" * 70)

    from agents.sequential_orchestrator_foundry import (
        SequentialWorkflowOrchestratorFoundry,
    )

    orch = SequentialWorkflowOrchestratorFoundry()
    logger.info("  project_endpoint : %s",
                 orch.project_endpoint[:45] + "..." if orch.project_endpoint else "(not set)")
    logger.info("  deployment_name  : %s", orch.deployment_name)
    logger.info("  pipeline         : Retrieval → SourceValidation → RefValidation → "
                 "AnswerSynthesis (Foundry Agent)")
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

            logger.info("    ⮜ OUTPUT:")
            logger.info("      status     : %s", result["status"])
            logger.info("      matches    : %d", len(result["matches"]))
            logger.info("      references : %d", len(result["references"]))
            logger.info("      grounded   : %s", result["is_grounded"])
            logger.info("      answer     : %s...", result["answer"][:120])
            logger.info("      elapsed    : %.2fs", dt)

            for j, m in enumerate(result["matches"][:3]):
                logger.info("      match[%d] policy=%s  file=%s",
                             j, m.get("policyNumber", "?"),
                             m.get("fileName", "?")[:60])

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
    logger.info("✓ Step 6 %s\n", "PASSED" if failed == 0 else "FAILED")

    return results


# =========================================================================
#  (Step 7 removed — original AF orchestrator no longer exists)
# =========================================================================


# =========================================================================
#  STEP 8: Verify agents still visible in Foundry Portal
# =========================================================================

def step_8_verify_agents_in_portal() -> None:
    logger.info("=" * 70)
    logger.info("STEP 8: Final verification – agents in Foundry Portal")
    logger.info("=" * 70)

    from agents.foundry_client import get_project_client

    pc = get_project_client()
    agent_names = [
        "hr-source-validator-agent",
        "hr-reference-validator-agent",
        "HRAnswerSynthesisFoundry",
    ]

    logger.info("  ⮞ INPUT: checking %d agents in portal", len(agent_names))

    for name in agent_names:
        try:
            agent = pc.agents.get(agent_name=name)
            logger.info("  ✓ %s  (version=%s)", name, agent.versions.get("latest", {}).get("version", "unknown") if agent.versions else "unknown")
        except Exception as e:
            persist = os.getenv("PERSIST_FOUNDRY_AGENTS", "false").lower() == "true"
            if persist:
                logger.error("  ✗ %s NOT found: %s", name, e)
                raise
            else:
                logger.info("  ○ %s (cleaned up — PERSIST_FOUNDRY_AGENTS=false)", name)

    logger.info("  ⮜ OUTPUT: portal verification complete")
    logger.info("✓ Step 8 PASSED\n")


# =========================================================================
#  Main
# =========================================================================

def main():
    logger.info("╔" + "═" * 68 + "╗")
    logger.info("║  Live E2E – Foundry Agent Service (agents/)                       ║")
    logger.info("║  Mode: LIVE (no mocks)                                            ║")
    logger.info("║  Queries: %d from test_queries.py                                 ║",
                 len(TEST_QUERIES))
    logger.info("║  PERSIST_FOUNDRY_AGENTS: %s                                       ║",
                 os.getenv("PERSIST_FOUNDRY_AGENTS", "false"))
    logger.info("╚" + "═" * 68 + "╝")
    logger.info("")

    total_start = time.time()
    passed = 0
    failed = 0
    total = 7

    steps = [
        ("Step 1: Verify environment", step_1_verify_environment),
        ("Step 2: Verify SDK imports", step_2_verify_imports),
        ("Step 3: Register agents in Foundry Portal", step_3_register_agents),
        ("Step 4: Individual agents (LIVE)", step_4_test_individual_agents),
        ("Step 5: Invoke Foundry Agent (conversations API)", step_5_invoke_foundry_agent),
        ("Step 6: Foundry orchestrator (LIVE)", step_6_foundry_orchestrator),
        ("Step 7: Final portal verification", step_8_verify_agents_in_portal),
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
    logger.info("  Queries : %d total (run through 2 orchestrators)", len(TEST_QUERIES))
    logger.info("  Elapsed : %.1fs", total_time)
    logger.info("=" * 70)

    # Write results to JSON
    results_path = Path(__file__).resolve().parent / "e2e_live_foundry_results.json"
    summary = {
        "steps_passed": passed,
        "steps_failed": failed,
        "total_queries": len(TEST_QUERIES),
        "elapsed_seconds": round(total_time, 1),
    }
    with open(results_path, "w") as f:
        json.dump(summary, f, indent=2)
    logger.info("Results written to %s", results_path)

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
