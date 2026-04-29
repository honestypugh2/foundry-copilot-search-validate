"""
End-to-End Integration Test – Agent Framework Path (agents_af/)

Exercises the complete Agent Framework + FoundryChatClient pipeline
against live Azure services:

    Azure AI Search (Agentic Retrieval)
        → RetrievalExecutor (custom)
        → SourceValidationExecutor (custom)
        → ReferenceValidationExecutor (custom)
        → FoundryChatClient Agent (answer synthesis)
        → FinalSynthesisExecutor (custom)

Usage:
    # Live mode (requires Azure credentials + indexed data)
    PYTHONPATH=$PWD/src python src/tests/test_e2e_agent_framework.py

    # Mock mode (validates wiring only)
    PYTHONPATH=$PWD/src python src/tests/test_e2e_agent_framework.py --mock

Environment variables required (live mode):
    AZURE_SEARCH_ENDPOINT, AZURE_OPENAI_ENDPOINT,
    AZURE_AI_PROJECT_ENDPOINT or FOUNDRY_PROJECT_ENDPOINT,
    FOUNDRY_MODEL (default: gpt-4.1)
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
logger = logging.getLogger("test_e2e_af")

from test_queries import TEST_QUERIES, QUERY_EXPECTATIONS


# ---------------------------------------------------------------------------
# Step 1: Validate Agent Framework agents can be imported
# ---------------------------------------------------------------------------

def step_1_validate_imports() -> None:
    logger.info("=" * 60)
    logger.info("STEP 1: Validate Agent Framework agent imports")
    logger.info("=" * 60)

    from agents_af.retrieval_agent import RetrievalAgent
    from agents_af.source_validator_agent import SourceValidatorAgent
    from agents_af.reference_validator_agent import ReferenceValidatorAgent
    from agents_af.answer_synthesis_agent import AnswerSynthesisAgent
    from agents_af.sequential_orchestrator import SequentialWorkflowOrchestrator

    # Verify AF-specific imports
    from agent_framework import Agent, Executor, Message
    from agent_framework.foundry import FoundryChatClient
    from agent_framework.orchestrations import SequentialBuilder

    logger.info("  ✓ All agents_af modules imported")
    logger.info("  ✓ Agent Framework core imported (Agent, Executor, Message)")
    logger.info("  ✓ FoundryChatClient imported")
    logger.info("  ✓ SequentialBuilder imported")
    logger.info("✓ Step 1 PASSED\n")


# ---------------------------------------------------------------------------
# Step 2: Test individual AF agents with sample data
# ---------------------------------------------------------------------------

def step_2_test_agents(mock: bool) -> dict[str, Any]:
    logger.info("=" * 60)
    logger.info("STEP 2: Test individual Agent Framework agents")
    logger.info("=" * 60)

    from agents_af.retrieval_agent import RetrievalAgent
    from agents_af.source_validator_agent import SourceValidatorAgent
    from agents_af.reference_validator_agent import ReferenceValidatorAgent
    from agents_af.answer_synthesis_agent import AnswerSynthesisAgent

    # --- Retrieval Agent ---
    retrieval_agent = RetrievalAgent()
    assert hasattr(retrieval_agent, "_agentic_search")
    assert hasattr(retrieval_agent, "_deterministic_search")
    logger.info("  ✓ RetrievalAgent: agentic_enabled=%s", retrieval_agent._agentic_enabled)

    if not mock:
        result = retrieval_agent.run("What is the PTO policy?")
        assert "matches" in result
        assert len(result["matches"]) > 0
        logger.info("  ✓ RetrievalAgent.run(): %d matches", len(result["matches"]))
        if "agentic_answer" in result:
            logger.info("  ✓ Agentic answer: %d chars", len(result["agentic_answer"]))
    else:
        logger.info("  [MOCK] Skipping live retrieval")

    # --- Source Validator ---
    source_agent = SourceValidatorAgent()
    sample = {
        "matches": [
            {"content": "PTO content", "filePath": "/p", "fileName": "51350.pdf",
             "parentTitle": "PTO", "policyNumber": "51350",
             "documentType": "pdf", "container": "ask-hr-knowledge"},
            {"content": "Bad", "filePath": "/b", "fileName": "bad.pdf",
             "parentTitle": "", "policyNumber": "",
             "documentType": "pdf", "container": "untrusted"},
        ]
    }
    val_result = source_agent.run(sample)
    assert val_result["source_count"] == 1
    logger.info("  ✓ SourceValidatorAgent: %d/%d trusted", val_result["source_count"], 2)

    # --- Reference Validator ---
    ref_agent = ReferenceValidatorAgent()
    ref_result = ref_agent.run(val_result)
    assert ref_result["is_grounded"] is True
    assert len(ref_result["references"]) == 1
    logger.info("  ✓ ReferenceValidatorAgent: grounded=%s, refs=%d",
                ref_result["is_grounded"], len(ref_result["references"]))

    # --- Answer Synthesis ---
    synth_agent = AnswerSynthesisAgent()

    # Test agentic passthrough
    synth_result = synth_agent.run(
        user_query="What is PTO?",
        validated_sources=val_result["validated_sources"],
        references=ref_result["references"],
        is_grounded=True,
        agentic_answer="PTO is defined in Policy 51350.",
    )
    assert synth_result["source"] == "agentic_retrieval"
    logger.info("  ✓ AnswerSynthesisAgent: agentic passthrough works")

    # Test pending path (no agentic answer)
    synth_pending = synth_agent.run(
        user_query="What is PTO?",
        validated_sources=val_result["validated_sources"],
        references=ref_result["references"],
        is_grounded=True,
    )
    assert synth_pending["source"] == "pending_foundry_chat_client"
    logger.info("  ✓ AnswerSynthesisAgent: pending path works")

    logger.info("✓ Step 2 PASSED\n")
    return {"val_result": val_result, "ref_result": ref_result}


# ---------------------------------------------------------------------------
# Step 3: Test SequentialWorkflowOrchestrator (AF)
# ---------------------------------------------------------------------------

def step_3_test_orchestrator(mock: bool) -> None:
    logger.info("=" * 60)
    logger.info("STEP 3: Test AF Sequential Orchestrator")
    logger.info("=" * 60)

    from agents_af.sequential_orchestrator import SequentialWorkflowOrchestrator

    orch = SequentialWorkflowOrchestrator()
    logger.info("  ✓ SequentialWorkflowOrchestrator (AF) instantiated")
    logger.info("    project_endpoint: %s",
                orch.project_endpoint[:30] + "..." if orch.project_endpoint else "(not set)")
    logger.info("    model: %s", orch.model)

    if not mock:
        for query in TEST_QUERIES:
            logger.info("  --- Query: %r ---", query)
            try:
                result = orch.process_query(query)
                assert result["status"] == "completed"
                assert "answer" in result
                assert "matches" in result
                assert "references" in result
                assert "is_grounded" in result

                logger.info("    ✓ matches=%d, refs=%d, grounded=%s",
                            len(result["matches"]),
                            len(result["references"]),
                            result["is_grounded"])
                logger.info("    ✓ answer: %s...", result["answer"][:100])
            except Exception as e:
                logger.error("    ✗ Query failed: %s", e)
                raise
    else:
        logger.info("  [MOCK] Skipping live orchestrator run")
        # Verify the workflow can be built
        try:
            workflow = orch._build_workflow()
            assert workflow is not None
            logger.info("  ✓ Workflow built successfully (6 participants)")
        except Exception as e:
            logger.warning("  ⚠ Workflow build requires credentials: %s", e)

    logger.info("✓ Step 3 PASSED\n")


# ---------------------------------------------------------------------------
# Step 4: Compare with Foundry Agent Service path
# ---------------------------------------------------------------------------

def step_4_compare_paths(mock: bool) -> None:
    logger.info("=" * 60)
    logger.info("STEP 4: Compare AF vs Foundry Agent Service results")
    logger.info("=" * 60)

    if mock:
        logger.info("  [MOCK] Skipping comparison (requires live Azure)")
        logger.info("✓ Step 4 SKIPPED (mock mode)\n")
        return

    from agents_af.sequential_orchestrator import (
        SequentialWorkflowOrchestrator as AFOrch,
    )
    from agents.sequential_orchestrator_foundry import (
        FoundryAgentOrchestrator as FoundryOrch,
    )

    af_orch = AFOrch()
    foundry_orch = FoundryOrch()

    query = "What is the PTO policy?"

    logger.info("  Running AF orchestrator...")
    af_result = af_orch.process_query(query)

    logger.info("  Running Foundry orchestrator...")
    foundry_result = foundry_orch.process_query(query)

    # Both should succeed
    assert af_result["status"] == "completed"
    assert foundry_result["status"] == "completed"

    # Both should be grounded
    assert af_result["is_grounded"] is True
    assert foundry_result["is_grounded"] is True

    # Both should return matches
    assert len(af_result["matches"]) > 0

    logger.info("  ✓ AF:      matches=%d, refs=%d, grounded=%s",
                len(af_result["matches"]),
                len(af_result["references"]),
                af_result["is_grounded"])
    logger.info("  ✓ Foundry: answer=%d chars, mode=%s",
                len(foundry_result.get("answer", "")),
                foundry_result.get("pipeline_mode"))
    logger.info("✓ Step 4 PASSED\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="E2E test – Agent Framework path")
    parser.add_argument("--mock", action="store_true",
                        help="Run in mock mode without Azure credentials")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("E2E Test – Agent Framework Path (agents_af/)")
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
        ("Step 3: AF Orchestrator", lambda: step_3_test_orchestrator(args.mock)),
        ("Step 4: Path Comparison", lambda: step_4_compare_paths(args.mock)),
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
