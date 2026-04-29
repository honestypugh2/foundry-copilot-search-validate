"""
Tests – Foundry Agent Service agents (agents/)

Dual-mode: connects to Azure by default, mocked with --mock flag.

Usage:
    # Live (default) – hits real Azure AI Search & Foundry
    PYTHONPATH=$PWD/src pytest src/tests/test_agents_foundry.py -v -s

    # Mock – no Azure needed
    PYTHONPATH=$PWD/src pytest src/tests/test_agents_foundry.py -v -s --mock
"""

import json
import logging
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

logger = logging.getLogger("test.agents_foundry")

from conftest import SAMPLE_MATCHES, SAMPLE_UNTRUSTED_MATCH
from test_queries import TEST_QUERIES


# ═══════════════════════════════════════════════════════════════════════════
#  RETRIEVAL AGENT (Foundry path)
# ═══════════════════════════════════════════════════════════════════════════

class TestRetrievalAgentFoundry:
    """agents.retrieval_agent.RetrievalAgent"""

    def test_instantiation(self, test_logger):
        test_logger.info("─── RetrievalAgent (Foundry): instantiation ───")
        from agents.retrieval_agent import RetrievalAgent

        agent = RetrievalAgent()
        assert hasattr(agent, "search_client")
        assert hasattr(agent, "_agentic_enabled")
        test_logger.info("  model           = %s", agent.model)
        test_logger.info("  agentic_enabled = %s", agent._agentic_enabled)
        test_logger.info("  ✓ instantiation OK")

    def test_mock_deterministic_fallback(self, test_logger):
        test_logger.info("─── RetrievalAgent (Foundry): deterministic fallback (mock) ───")

        with patch("agents.retrieval_agent.AzureAISearchClient") as MockClient:
            mock_instance = MockClient.return_value
            mock_instance.hybrid_search.return_value = SAMPLE_MATCHES

            from agents.retrieval_agent import RetrievalAgent

            agent = RetrievalAgent()
            agent.search_client = mock_instance
            agent._agentic_enabled = False

            result = agent.run("What is the dress code?")

        assert "matches" in result
        assert len(result["matches"]) == 2
        test_logger.info("  matches : %d", len(result["matches"]))
        test_logger.info("  ✓ deterministic fallback OK (mock)")

    def test_mock_agentic_primary(self, test_logger):
        test_logger.info("─── RetrievalAgent (Foundry): agentic search (mock) ───")

        with patch("agents.retrieval_agent.AzureAISearchClient") as MockClient:
            mock_instance = MockClient.return_value
            mock_instance.agentic_retrieve.return_value = {
                "matches": SAMPLE_MATCHES,
                "response": "Dress code is in Policy 52005.",
                "activity": [],
            }

            from agents.retrieval_agent import RetrievalAgent

            agent = RetrievalAgent()
            agent.search_client = mock_instance
            agent._agentic_enabled = True

            result = agent.run("What is the dress code?")

        assert result["agentic_answer"] == "Dress code is in Policy 52005."
        test_logger.info("  agentic_answer : %s", result["agentic_answer"][:60])
        test_logger.info("  ✓ agentic search OK (mock)")

    def test_live_retrieval(self, is_live, test_logger):
        if not is_live:
            pytest.skip("--mock mode")

        test_logger.info("─── RetrievalAgent (Foundry): LIVE retrieval ───")
        from agents.retrieval_agent import RetrievalAgent

        agent = RetrievalAgent()
        result = agent.run(TEST_QUERIES[0])

        assert "matches" in result
        assert len(result["matches"]) > 0
        test_logger.info("  matches : %d", len(result["matches"]))
        if "agentic_answer" in result:
            test_logger.info("  agentic : %s...", result["agentic_answer"][:80])
        test_logger.info("  ✓ LIVE retrieval OK")


# ═══════════════════════════════════════════════════════════════════════════
#  SOURCE VALIDATOR AGENT (Foundry path)
# ═══════════════════════════════════════════════════════════════════════════

class TestSourceValidatorAgentFoundry:
    """agents.source_validator_agent.SourceValidatorAgent"""

    def test_instantiation(self, test_logger):
        test_logger.info("─── SourceValidatorAgent (Foundry): instantiation ───")
        from agents.source_validator_agent import SourceValidatorAgent

        agent = SourceValidatorAgent()
        assert agent.AGENT_NAME == "hr-source-validator-agent"
        test_logger.info("  agent_name = %s", agent.AGENT_NAME)
        test_logger.info("  model      = %s", agent.model)
        test_logger.info("  ✓ instantiation OK")

    def test_deterministic_filter(self, test_logger):
        test_logger.info("─── SourceValidatorAgent (Foundry): deterministic filter ───")
        from agents.source_validator_agent import SourceValidatorAgent

        agent = SourceValidatorAgent()
        result = agent._deterministic_validate(
            {"matches": SAMPLE_MATCHES + [SAMPLE_UNTRUSTED_MATCH]}
        )

        assert result["source_count"] == 2
        for src in result["validated_sources"]:
            assert src["container"] == "ask-hr-knowledge"
        test_logger.info("  validated : %d / %d", result["source_count"], 3)
        test_logger.info("  ✓ deterministic filter OK")

    def test_empty_matches(self, test_logger):
        test_logger.info("─── SourceValidatorAgent (Foundry): empty input ───")
        from agents.source_validator_agent import SourceValidatorAgent

        result = SourceValidatorAgent()._deterministic_validate({"matches": []})
        assert result["source_count"] == 0
        test_logger.info("  ✓ empty input handled")

    @patch("agents.source_validator_agent.AGENTS_SDK_AVAILABLE", False)
    def test_run_without_sdk_uses_deterministic(self, test_logger):
        test_logger.info("─── SourceValidatorAgent (Foundry): no SDK → deterministic ───")
        from agents.source_validator_agent import SourceValidatorAgent

        agent = SourceValidatorAgent()
        agent.project_endpoint = ""
        result = agent.run({"matches": SAMPLE_MATCHES})

        assert result["source_count"] == 2
        test_logger.info("  ✓ deterministic-only mode OK")

    def test_live_source_validation(self, is_live, test_logger):
        if not is_live:
            pytest.skip("--mock mode")

        test_logger.info("─── SourceValidatorAgent (Foundry): LIVE ───")
        from agents.retrieval_agent import RetrievalAgent
        from agents.source_validator_agent import SourceValidatorAgent

        retrieval = RetrievalAgent().run(TEST_QUERIES[0])
        result = SourceValidatorAgent().run(retrieval)

        assert result["source_count"] > 0
        test_logger.info("  validated : %d / %d", result["source_count"],
                         len(retrieval["matches"]))
        test_logger.info("  ✓ LIVE source validation OK")


# ═══════════════════════════════════════════════════════════════════════════
#  REFERENCE VALIDATOR AGENT (Foundry path)
# ═══════════════════════════════════════════════════════════════════════════

class TestReferenceValidatorAgentFoundry:
    """agents.reference_validator_agent.ReferenceValidatorAgent"""

    def test_deterministic_extract(self, test_logger):
        test_logger.info("─── ReferenceValidatorAgent (Foundry): extract ───")
        from agents.reference_validator_agent import ReferenceValidatorAgent

        result = ReferenceValidatorAgent()._deterministic_extract(
            {"validated_sources": SAMPLE_MATCHES}
        )

        assert result["is_grounded"] is True
        assert len(result["references"]) == 2
        assert result["references"][0]["policyNumber"] == "51350"
        test_logger.info("  grounded   : %s", result["is_grounded"])
        test_logger.info("  references : %d", len(result["references"]))
        test_logger.info("  ✓ extract OK")

    def test_no_sources_not_grounded(self, test_logger):
        test_logger.info("─── ReferenceValidatorAgent (Foundry): ungrounded ───")
        from agents.reference_validator_agent import ReferenceValidatorAgent

        result = ReferenceValidatorAgent()._deterministic_extract(
            {"validated_sources": []}
        )
        assert result["is_grounded"] is False
        test_logger.info("  ✓ correctly ungrounded")

    def test_live_reference_validation(self, is_live, test_logger):
        if not is_live:
            pytest.skip("--mock mode")

        test_logger.info("─── ReferenceValidatorAgent (Foundry): LIVE pipeline ───")
        from agents.retrieval_agent import RetrievalAgent
        from agents.source_validator_agent import SourceValidatorAgent
        from agents.reference_validator_agent import ReferenceValidatorAgent

        retrieval = RetrievalAgent().run(TEST_QUERIES[1])
        validated = SourceValidatorAgent().run(retrieval)
        result = ReferenceValidatorAgent().run(validated)

        assert result["is_grounded"] is True
        assert len(result["references"]) > 0
        test_logger.info("  grounded   : %s", result["is_grounded"])
        test_logger.info("  references : %d", len(result["references"]))
        test_logger.info("  ✓ LIVE reference validation OK")


# ═══════════════════════════════════════════════════════════════════════════
#  ANSWER SYNTHESIS AGENT (Foundry path)
# ═══════════════════════════════════════════════════════════════════════════

class TestAnswerSynthesisAgentFoundry:
    """agents.answer_synthesis_agent.AnswerSynthesisAgent"""

    def test_instantiation(self, test_logger):
        test_logger.info("─── AnswerSynthesisAgent (Foundry): instantiation ───")
        from agents.answer_synthesis_agent import AnswerSynthesisAgent

        agent = AnswerSynthesisAgent()
        assert agent.model, "model is empty"
        assert hasattr(agent, 'synthesize_answer'), \
            "Missing synthesize_answer method"
        assert hasattr(agent, '_build_system_instructions'), \
            "Missing _build_system_instructions method"
        assert hasattr(agent, '_build_context_message'), \
            "Missing _build_context_message method"
        test_logger.info("  model : %s", agent.model)
        test_logger.info("  ✓ instantiation OK")

    def test_context_building(self, test_logger):
        test_logger.info("─── AnswerSynthesisAgent (Foundry): context ───")
        from agents.answer_synthesis_agent import AnswerSynthesisAgent

        ctx = AnswerSynthesisAgent()._build_context_message(
            ["PTO is accrued at 1.5 days per month."],
            [{"source_file": "51350.pdf", "policyNumber": "51350"}],
        )

        assert "Source [1]" in ctx
        assert "51350.pdf" in ctx
        test_logger.info("  context : %d chars", len(ctx))
        test_logger.info("  ✓ context built OK")

    def test_system_instructions(self, test_logger):
        test_logger.info("─── AnswerSynthesisAgent (Foundry): instructions ───")
        from agents.answer_synthesis_agent import AnswerSynthesisAgent

        instructions = AnswerSynthesisAgent()._build_system_instructions()
        assert "HR Policy" in instructions
        assert len(instructions) > 50
        test_logger.info("  instructions : %d chars", len(instructions))
        test_logger.info("  ✓ instructions OK")


# ═══════════════════════════════════════════════════════════════════════════
#  FOUNDRY AGENT ORCHESTRATOR (single-agent MCP — MS recommended)
# ═══════════════════════════════════════════════════════════════════════════


# ═══════════════════════════════════════════════════════════════════════════
#  SEQUENTIAL ORCHESTRATOR – Foundry Agent Service
# ═══════════════════════════════════════════════════════════════════════════

class TestFoundryAgentOrchestrator:
    """agents.sequential_orchestrator_foundry.FoundryAgentOrchestrator"""

    def test_instantiation(self, test_logger):
        test_logger.info("─── FoundryAgentOrchestrator: instantiation ───")
        from agents.sequential_orchestrator_foundry import FoundryAgentOrchestrator

        orch = FoundryAgentOrchestrator()
        assert hasattr(orch, "process_query_async")
        assert hasattr(orch, "process_query")
        assert hasattr(orch, "_build_mcp_tool")
        assert hasattr(orch, "_build_instructions")
        test_logger.info("  project : %s",
                         orch.project_endpoint[:40] + "..." if orch.project_endpoint else "(not set)")
        test_logger.info("  mode    : %s", orch._pipeline_mode)
        test_logger.info("  ✓ instantiation OK")

    def test_instructions(self, test_logger):
        test_logger.info("─── FoundryAgentOrchestrator: instructions ───")
        from agents.sequential_orchestrator_foundry import FoundryAgentOrchestrator

        instructions = FoundryAgentOrchestrator()._build_instructions()
        assert len(instructions) > 0
        assert "knowledge" in instructions.lower()
        test_logger.info("  instructions : %d chars", len(instructions))
        test_logger.info("  ✓ instructions OK")

    def test_citation_validation(self, test_logger):
        test_logger.info("─── FoundryAgentOrchestrator: citation validation ───")
        from agents.sequential_orchestrator_foundry import FoundryAgentOrchestrator

        orch = FoundryAgentOrchestrator()
        result = orch._validate_citation_annotations(
            "Answer with 【0:1†policy.docx】 and 【0:2†pto.docx】"
        )
        assert result["has_citations"] is True
        assert result["citation_count"] == 2
        test_logger.info("  citations: %d", result["citation_count"])
        test_logger.info("  ✓ citation validation OK")

    def test_live_orchestrator(self, is_live, test_logger):
        if not is_live:
            pytest.skip("--mock mode")

        test_logger.info("─── FoundryAgentOrchestrator: LIVE pipeline ───")
        from agents.sequential_orchestrator_foundry import FoundryAgentOrchestrator

        result = FoundryAgentOrchestrator().process_query(TEST_QUERIES[0])

        assert result["status"] == "completed"
        assert result["is_grounded"] is True
        test_logger.info("  status   : %s", result["status"])
        test_logger.info("  mode     : %s", result.get("pipeline_mode"))
        test_logger.info("  answer   : %s...", result.get("answer", "")[:80])
        test_logger.info("  ✓ LIVE FoundryAgentOrchestrator OK")


# ---------------------------------------------------------------------------
# (Executor tests removed — Agent Framework executors no longer used)
# ---------------------------------------------------------------------------
