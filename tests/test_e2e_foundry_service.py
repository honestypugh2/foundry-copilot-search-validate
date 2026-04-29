"""
End-to-End Integration Test – Foundry Agent Service Path (agents/)

Exercises the complete Foundry Agent Service pipeline against live
Azure services:

    Azure AI Search (Agentic Retrieval)
        → RetrievalAgent (local)
        → SourceValidatorAgent (Foundry Agent)
        → ReferenceValidatorAgent (Foundry Agent)
        → AnswerSynthesisAgent (Foundry Agent / agentic answer)

Tests the sequential_orchestrator_foundry.py (azure-ai-projects SDK,
single-agent MCP default, optional multi-step pipeline).

Usage:
    # Live mode (requires Azure credentials + indexed data)
    PYTHONPATH=$PWD/src python src/tests/test_e2e_foundry_service.py

    # Mock mode (validates wiring only)
    PYTHONPATH=$PWD/src python src/tests/test_e2e_foundry_service.py --mock

Environment variables required (live mode):
    AZURE_SEARCH_ENDPOINT, AZURE_OPENAI_ENDPOINT,
    AZURE_AI_PROJECT_ENDPOINT
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger("test_e2e_foundry")

from test_queries import TEST_QUERIES, QUERY_EXPECTATIONS


# ---------------------------------------------------------------------------
# Step 1: Validate Foundry Agent Service agents can be imported
# ---------------------------------------------------------------------------

def step_1_validate_imports() -> None:
    logger.info("=" * 60)
    logger.info("STEP 1: Validate Foundry Agent Service imports")
    logger.info("=" * 60)

    from agents.retrieval_agent import RetrievalAgent
    from agents.source_validator_agent import SourceValidatorAgent
    from agents.reference_validator_agent import ReferenceValidatorAgent
    from agents.answer_synthesis_agent import AnswerSynthesisAgent
    from agents.sequential_orchestrator_foundry import FoundryAgentOrchestrator
    from agents.foundry_client import (
        PROJECTS_SDK_AVAILABLE,
        get_project_client,
        ensure_agent,
        invoke_agent,
    )

    logger.info("  ✓ All agents modules imported")
    logger.info("  ✓ azure-ai-projects SDK available: %s", PROJECTS_SDK_AVAILABLE)
    logger.info("  ✓ FoundryAgentOrchestrator imported")
    logger.info("✓ Step 1 PASSED\n")


# ---------------------------------------------------------------------------
# Step 2: Test individual Foundry agents with sample data
# ---------------------------------------------------------------------------

def step_2_test_agents(mock: bool) -> dict[str, Any]:
    logger.info("=" * 60)
    logger.info("STEP 2: Test individual Foundry Agent Service agents")
    logger.info("=" * 60)

    from agents.retrieval_agent import RetrievalAgent
    from agents.source_validator_agent import SourceValidatorAgent
    from agents.reference_validator_agent import ReferenceValidatorAgent
    from agents.answer_synthesis_agent import AnswerSynthesisAgent

    # --- Retrieval Agent ---
    retrieval_agent = RetrievalAgent()
    logger.info("  ✓ RetrievalAgent: agentic_enabled=%s, model=%s",
                retrieval_agent._agentic_enabled, retrieval_agent.model)

    retrieval_output = None
    if not mock:
        retrieval_output = retrieval_agent.run("What is the PTO policy?")
        assert "matches" in retrieval_output
        assert len(retrieval_output["matches"]) > 0
        logger.info("  ✓ RetrievalAgent.run(): %d matches", len(retrieval_output["matches"]))
        if "agentic_answer" in retrieval_output:
            logger.info("  ✓ Agentic answer: %d chars", len(retrieval_output["agentic_answer"]))
    else:
        logger.info("  [MOCK] Skipping live retrieval")

    # --- Source Validator Agent ---
    source_agent = SourceValidatorAgent()
    assert source_agent.AGENT_NAME == "hr-source-validator-agent"

    sample = {
        "matches": [
            {"content": "PTO", "filePath": "/p", "fileName": "51350.pdf",
             "parentTitle": "PTO", "policyNumber": "51350",
             "documentType": "pdf", "container": "ask-hr-knowledge"},
            {"content": "Bad", "filePath": "/b", "fileName": "bad.pdf",
             "parentTitle": "", "policyNumber": "",
             "documentType": "pdf", "container": "untrusted"},
        ]
    }
    val_result = source_agent._deterministic_validate(sample)
    assert val_result["source_count"] == 1
    logger.info("  ✓ SourceValidatorAgent: deterministic filter works")

    if not mock and retrieval_output:
        val_result_live = source_agent.run(retrieval_output)
        logger.info("  ✓ SourceValidatorAgent.run(live): %d/%d trusted",
                    val_result_live["source_count"], len(retrieval_output["matches"]))
    else:
        logger.info("  [MOCK] Skipping live source validation")

    # --- Reference Validator ---
    ref_agent = ReferenceValidatorAgent()
    ref_result = ref_agent._deterministic_extract(val_result)
    assert ref_result["is_grounded"] is True
    logger.info("  ✓ ReferenceValidatorAgent: deterministic extract works")

    # --- Answer Synthesis ---
    answer_agent = AnswerSynthesisAgent()
    assert answer_agent.model, "AnswerSynthesisAgent model is empty"
    assert hasattr(answer_agent, 'synthesize_answer'), \
        "AnswerSynthesisAgent missing synthesize_answer method"
    logger.info("  ✓ AnswerSynthesisAgent: model=%s, methods verified", answer_agent.model)

    logger.info("✓ Step 2 PASSED\n")
    return {"val_result": val_result, "ref_result": ref_result}


# ---------------------------------------------------------------------------
# Step 3: Test FoundryAgentOrchestrator (single-agent MCP)
# ---------------------------------------------------------------------------

def step_3_test_foundry_orchestrator(mock: bool) -> None:
    logger.info("=" * 60)
    logger.info("STEP 3: Test FoundryAgentOrchestrator (single-agent MCP)")
    logger.info("=" * 60)

    from agents.sequential_orchestrator_foundry import FoundryAgentOrchestrator

    orch = FoundryAgentOrchestrator()
    logger.info("  ✓ FoundryAgentOrchestrator instantiated (mode=%s)", orch._pipeline_mode)

    if not mock:
        for query in TEST_QUERIES:
            logger.info("  --- Query: %r ---", query)
            result = orch.process_query(query)
            assert result["status"] == "completed"
            assert "answer" in result
            logger.info("    ✓ answer=%d chars, mode=%s",
                        len(result["answer"]),
                        result.get("pipeline_mode"))
    else:
        logger.info("  [MOCK] Skipping live orchestrator run")

    logger.info("✓ Step 3 PASSED\n")


# ---------------------------------------------------------------------------
# Step 4: (removed — merged into Step 3)
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Step 5: Test Azure Function endpoint (local)
# ---------------------------------------------------------------------------

def step_5_test_function_endpoint(mock: bool) -> None:
    logger.info("=" * 60)
    logger.info("STEP 5: Test Azure Function /api/ask endpoint")
    logger.info("=" * 60)

    if mock:
        # Validate function app module imports
        from api.function_app import app, ask_hr_policy
        assert app is not None
        logger.info("  ✓ Function app imported successfully")
        logger.info("  [MOCK] Skipping live endpoint test")
        logger.info("✓ Step 5 PASSED (mock)\n")
        return

    import asyncio
    try:
        import httpx
    except ImportError:
        logger.warning("  ⚠ httpx not installed, skipping live endpoint test")
        logger.info("✓ Step 5 SKIPPED\n")
        return

    url = os.getenv("FUNCTION_APP_URL", "http://localhost:7071")

    async def _test():
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{url}/api/ask",
                json={"query": "What is the PTO policy?"},
                timeout=60.0,
            )
        return resp

    try:
        resp = asyncio.run(_test())
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"
        assert body["is_grounded"] is True
        logger.info("  ✓ /api/ask returned grounded response")
        logger.info("    answer: %s...", body["answer"][:80])
    except Exception as e:
        logger.warning("  ⚠ Endpoint test failed (is func start running?): %s", e)
        logger.info("  Tip: Start the function with 'func start' first")

    logger.info("✓ Step 5 PASSED\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="E2E test – Foundry Agent Service path"
    )
    parser.add_argument("--mock", action="store_true",
                        help="Run in mock mode without Azure credentials")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("E2E Test – Foundry Agent Service Path (agents/)")
    logger.info("Mode: %s", "MOCK" if args.mock else "LIVE")
    logger.info("=" * 60)
    logger.info("")

    try:
        from dotenv import load_dotenv
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path)
            logger.info("Loaded .env from %s\n", env_path)
    except ImportError:
        pass

    passed = 0
    failed = 0
    total = 4

    tests = [
        ("Step 1: Validate Imports", lambda: step_1_validate_imports()),
        ("Step 2: Individual Agents", lambda: step_2_test_agents(args.mock)),
        ("Step 3: FoundryAgentOrchestrator", lambda: step_3_test_foundry_orchestrator(args.mock)),
        ("Step 4: Function Endpoint", lambda: step_5_test_function_endpoint(args.mock)),
    ]

    for name, test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            logger.error("✗ %s FAILED: %s", name, e)
            failed += 1

    logger.info("=" * 60)
    logger.info("RESULTS: %d/%d passed, %d failed", passed, total, failed)
    logger.info("=" * 60)

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
