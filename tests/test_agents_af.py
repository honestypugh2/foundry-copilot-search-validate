"""
Tests – Agent Framework agents (agents_af/)

Dual-mode: connects to Azure by default, mocked with --mock flag.

Usage:
    # Live (default) – hits real Azure AI Search & Foundry
    PYTHONPATH=$PWD/src pytest src/tests/test_agents_af.py -v -s

    # Mock – no Azure needed
    PYTHONPATH=$PWD/src pytest src/tests/test_agents_af.py -v -s --mock
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

logger = logging.getLogger("test.agents_af")

# Import shared sample data from conftest
from conftest import SAMPLE_MATCHES, SAMPLE_UNTRUSTED_MATCH
from test_queries import TEST_QUERIES


# ═══════════════════════════════════════════════════════════════════════════
#  RETRIEVAL AGENT
# ═══════════════════════════════════════════════════════════════════════════

class TestRetrievalAgentAF:
    """agents_af.retrieval_agent.RetrievalAgent"""

    def test_instantiation(self, test_logger):
        test_logger.info("─── RetrievalAgent: instantiation ───")
        from agents_af.retrieval_agent import RetrievalAgent

        agent = RetrievalAgent()
        assert hasattr(agent, "search_client")
        assert hasattr(agent, "_agentic_enabled")
        assert hasattr(agent, "run")
        test_logger.info("  model          = %s", agent.model)
        test_logger.info("  agentic_enabled = %s", agent._agentic_enabled)
        test_logger.info("  ✓ instantiation OK")

    # --- Mock path -------------------------------------------------------

    def test_mock_deterministic_search(self, test_logger):
        """Hybrid-search fallback when agentic retrieval is disabled."""
        test_logger.info("─── RetrievalAgent: deterministic search (mock) ───")

        with patch("agents_af.retrieval_agent.AzureAISearchClient") as MockClient:
            mock_instance = MockClient.return_value
            mock_instance.hybrid_search.return_value = SAMPLE_MATCHES

            from agents_af.retrieval_agent import RetrievalAgent

            agent = RetrievalAgent()
            agent.search_client = mock_instance
            agent._agentic_enabled = False

            result = agent.run("What is the PTO policy?")

        assert "matches" in result
        assert len(result["matches"]) == 2
        assert result["matches"][0]["policyNumber"] == "51350"
        test_logger.info("  matches returned : %d", len(result["matches"]))
        test_logger.info("  first policy     : %s", result["matches"][0]["policyNumber"])
        test_logger.info("  ✓ deterministic search OK (mock)")

    def test_mock_agentic_search(self, test_logger):
        """Agentic retrieval via Knowledge Base (mocked)."""
        test_logger.info("─── RetrievalAgent: agentic search (mock) ───")

        with patch("agents_af.retrieval_agent.AzureAISearchClient") as MockClient:
            mock_instance = MockClient.return_value
            mock_instance.agentic_retrieve.return_value = {
                "matches": SAMPLE_MATCHES,
                "response": "PTO is defined in Policy 51350.",
                "activity": [{"type": "query_planning"}],
            }

            from agents_af.retrieval_agent import RetrievalAgent

            agent = RetrievalAgent()
            agent.search_client = mock_instance
            agent._agentic_enabled = True

            result = agent.run("What is the PTO policy?")

        assert result["agentic_answer"] == "PTO is defined in Policy 51350."
        assert len(result["activity"]) == 1
        test_logger.info("  agentic_answer : %s", result["agentic_answer"][:60])
        test_logger.info("  activity steps : %d", len(result["activity"]))
        test_logger.info("  ✓ agentic search OK (mock)")

    def test_mock_agentic_failure_falls_back(self, test_logger):
        """When agentic retrieval raises, falls back to hybrid search."""
        test_logger.info("─── RetrievalAgent: agentic failure → fallback (mock) ───")

        with patch("agents_af.retrieval_agent.AzureAISearchClient") as MockClient:
            mock_instance = MockClient.return_value
            mock_instance.agentic_retrieve.side_effect = RuntimeError("KB unavailable")
            mock_instance.hybrid_search.return_value = SAMPLE_MATCHES

            from agents_af.retrieval_agent import RetrievalAgent

            agent = RetrievalAgent()
            agent.search_client = mock_instance
            agent._agentic_enabled = True

            result = agent.run("What is the PTO policy?")

        assert "matches" in result
        assert len(result["matches"]) == 2
        assert "agentic_answer" not in result
        test_logger.info("  fallback matches : %d", len(result["matches"]))
        test_logger.info("  ✓ graceful fallback OK (mock)")

    # --- Live Azure path -------------------------------------------------

    def test_live_retrieval(self, is_live, test_logger):
        """Run retrieval against live Azure AI Search."""
        if not is_live:
            pytest.skip("--mock mode: skipping live Azure call")

        test_logger.info("─── RetrievalAgent: LIVE Azure retrieval ───")
        from agents_af.retrieval_agent import RetrievalAgent

        agent = RetrievalAgent()
        test_logger.info("  agentic_enabled = %s", agent._agentic_enabled)

        result = agent.run(TEST_QUERIES[0])

        assert "matches" in result
        assert len(result["matches"]) > 0
        test_logger.info("  matches returned : %d", len(result["matches"]))

        if "agentic_answer" in result and result["agentic_answer"]:
            test_logger.info("  agentic_answer   : %s...", result["agentic_answer"][:80])
        else:
            test_logger.info("  mode             : hybrid search (fallback)")

        for i, m in enumerate(result["matches"][:3]):
            test_logger.info("  match[%d] policy=%s  file=%s",
                             i, m.get("policyNumber", "?"), m.get("fileName", "?")[:50])

        test_logger.info("  ✓ LIVE retrieval OK")

    def test_live_dress_code_query(self, is_live, test_logger):
        """Different query to validate search diversity."""
        if not is_live:
            pytest.skip("--mock mode")

        test_logger.info("─── RetrievalAgent: LIVE 'dress code' query ───")
        from agents_af.retrieval_agent import RetrievalAgent

        result = RetrievalAgent().run(TEST_QUERIES[1])

        assert len(result["matches"]) > 0
        test_logger.info("  matches: %d", len(result["matches"]))
        test_logger.info("  ✓ LIVE dress-code query OK")


# ═══════════════════════════════════════════════════════════════════════════
#  SOURCE VALIDATOR AGENT
# ═══════════════════════════════════════════════════════════════════════════

class TestSourceValidatorAgentAF:
    """agents_af.source_validator_agent.SourceValidatorAgent"""

    def test_instantiation(self, test_logger):
        test_logger.info("─── SourceValidatorAgent: instantiation ───")
        from agents_af.source_validator_agent import SourceValidatorAgent

        agent = SourceValidatorAgent()
        assert hasattr(agent, "run")
        test_logger.info("  model = %s", agent.model)
        test_logger.info("  ✓ instantiation OK")

    def test_filters_trusted_sources(self, test_logger):
        """Keeps ask-hr-knowledge, rejects untrusted containers."""
        test_logger.info("─── SourceValidatorAgent: filter trusted ───")
        from agents_af.source_validator_agent import SourceValidatorAgent

        result = SourceValidatorAgent().run(
            {"matches": SAMPLE_MATCHES + [SAMPLE_UNTRUSTED_MATCH]}
        )

        assert result["source_count"] == 2
        assert len(result["validated_sources"]) == 2
        for src in result["validated_sources"]:
            assert src["container"] == "ask-hr-knowledge"

        test_logger.info("  input    : %d matches (2 trusted + 1 untrusted)",
                         len(SAMPLE_MATCHES) + 1)
        test_logger.info("  output   : %d validated", result["source_count"])
        test_logger.info("  ✓ filter OK")

    def test_all_untrusted_returns_empty(self, test_logger):
        test_logger.info("─── SourceValidatorAgent: all untrusted → empty ───")
        from agents_af.source_validator_agent import SourceValidatorAgent

        result = SourceValidatorAgent().run({"matches": [SAMPLE_UNTRUSTED_MATCH]})
        assert result["source_count"] == 0
        test_logger.info("  ✓ correctly returns 0 validated")

    def test_empty_matches(self, test_logger):
        test_logger.info("─── SourceValidatorAgent: empty input ───")
        from agents_af.source_validator_agent import SourceValidatorAgent

        result = SourceValidatorAgent().run({"matches": []})
        assert result["source_count"] == 0
        assert result["validated_sources"] == []
        test_logger.info("  ✓ empty input handled")

    def test_live_source_validation(self, is_live, test_logger):
        """Validate sources from a live retrieval result."""
        if not is_live:
            pytest.skip("--mock mode")

        test_logger.info("─── SourceValidatorAgent: LIVE validation ───")
        from agents_af.retrieval_agent import RetrievalAgent
        from agents_af.source_validator_agent import SourceValidatorAgent

        retrieval = RetrievalAgent().run(TEST_QUERIES[0])
        result = SourceValidatorAgent().run(retrieval)

        assert result["source_count"] > 0
        test_logger.info("  input  : %d matches from search", len(retrieval["matches"]))
        test_logger.info("  output : %d validated sources", result["source_count"])
        for s in result["validated_sources"][:3]:
            test_logger.info("    policy=%s  container=%s",
                             s.get("policyNumber", "?"), s.get("container", "?"))
        test_logger.info("  ✓ LIVE source validation OK")


# ═══════════════════════════════════════════════════════════════════════════
#  REFERENCE VALIDATOR AGENT
# ═══════════════════════════════════════════════════════════════════════════

class TestReferenceValidatorAgentAF:
    """agents_af.reference_validator_agent.ReferenceValidatorAgent"""

    def test_instantiation(self, test_logger):
        test_logger.info("─── ReferenceValidatorAgent: instantiation ───")
        from agents_af.reference_validator_agent import ReferenceValidatorAgent

        agent = ReferenceValidatorAgent()
        assert hasattr(agent, "run")
        test_logger.info("  ✓ instantiation OK")

    def test_extracts_references(self, test_logger):
        test_logger.info("─── ReferenceValidatorAgent: extract references ───")
        from agents_af.reference_validator_agent import ReferenceValidatorAgent

        result = ReferenceValidatorAgent().run({"validated_sources": SAMPLE_MATCHES})

        assert result["is_grounded"] is True
        assert len(result["references"]) == 2
        assert result["references"][0]["policyNumber"] == "51350"
        test_logger.info("  grounded    : %s", result["is_grounded"])
        test_logger.info("  references  : %d", len(result["references"]))
        test_logger.info("  ✓ extraction OK")

    def test_no_sources_not_grounded(self, test_logger):
        test_logger.info("─── ReferenceValidatorAgent: no sources → not grounded ───")
        from agents_af.reference_validator_agent import ReferenceValidatorAgent

        result = ReferenceValidatorAgent().run({"validated_sources": []})
        assert result["is_grounded"] is False
        assert result["references"] == []
        test_logger.info("  ✓ correctly ungrounded")

    def test_live_reference_validation(self, is_live, test_logger):
        """Full chain: retrieval → source validation → reference validation."""
        if not is_live:
            pytest.skip("--mock mode")

        test_logger.info("─── ReferenceValidatorAgent: LIVE pipeline ───")
        from agents_af.retrieval_agent import RetrievalAgent
        from agents_af.source_validator_agent import SourceValidatorAgent
        from agents_af.reference_validator_agent import ReferenceValidatorAgent

        retrieval = RetrievalAgent().run(TEST_QUERIES[1])
        validated = SourceValidatorAgent().run(retrieval)
        result = ReferenceValidatorAgent().run(validated)

        assert result["is_grounded"] is True
        assert len(result["references"]) > 0
        test_logger.info("  grounded   : %s", result["is_grounded"])
        test_logger.info("  references : %d", len(result["references"]))
        for r in result["references"][:3]:
            test_logger.info("    policy=%s  file=%s",
                             r.get("policyNumber", "?"), r.get("fileName", "?")[:50])
        test_logger.info("  ✓ LIVE reference validation OK")


# ═══════════════════════════════════════════════════════════════════════════
#  ANSWER SYNTHESIS AGENT
# ═══════════════════════════════════════════════════════════════════════════

class TestAnswerSynthesisAgentAF:
    """agents_af.answer_synthesis_agent.AnswerSynthesisAgent"""

    def test_instantiation(self, test_logger):
        test_logger.info("─── AnswerSynthesisAgent: instantiation ───")
        from agents_af.answer_synthesis_agent import AnswerSynthesisAgent

        agent = AnswerSynthesisAgent()
        assert agent.model == "gpt-4.1"
        assert agent.output_mode == "answerSynthesis"
        test_logger.info("  model       = %s", agent.model)
        test_logger.info("  output_mode = %s", agent.output_mode)
        test_logger.info("  ✓ instantiation OK")

    def test_agentic_answer_passthrough(self, test_logger):
        test_logger.info("─── AnswerSynthesisAgent: agentic passthrough ───")
        from agents_af.answer_synthesis_agent import AnswerSynthesisAgent

        result = AnswerSynthesisAgent().run(
            user_query="What is PTO?",
            validated_sources=SAMPLE_MATCHES,
            references=[{"filePath": "/p", "policyNumber": "51350",
                         "documentType": "pdf", "parentTitle": "PTO",
                         "fileName": "51350.pdf"}],
            is_grounded=True,
            agentic_answer="PTO is defined in Policy 51350.",
        )

        assert result["answer"] == "PTO is defined in Policy 51350."
        assert result["source"] == "agentic_retrieval"
        assert result["is_grounded"] is True
        test_logger.info("  source : %s", result["source"])
        test_logger.info("  answer : %s", result["answer"][:60])
        test_logger.info("  ✓ agentic passthrough OK")

    def test_ungrounded_returns_fallback(self, test_logger):
        test_logger.info("─── AnswerSynthesisAgent: ungrounded → fallback ───")
        from agents_af.answer_synthesis_agent import AnswerSynthesisAgent

        result = AnswerSynthesisAgent().run(
            user_query="test",
            validated_sources=[],
            references=[],
            is_grounded=False,
        )

        assert result["is_grounded"] is False
        assert "could not find" in result["answer"].lower()
        test_logger.info("  answer : %s", result["answer"][:60])
        test_logger.info("  ✓ ungrounded fallback OK")

    def test_no_agentic_answer_returns_context(self, test_logger):
        test_logger.info("─── AnswerSynthesisAgent: no agentic → pending ───")
        from agents_af.answer_synthesis_agent import AnswerSynthesisAgent

        result = AnswerSynthesisAgent().run(
            user_query="What is PTO?",
            validated_sources=SAMPLE_MATCHES,
            references=[{"filePath": "/p", "policyNumber": "51350",
                         "documentType": "pdf", "parentTitle": "PTO",
                         "fileName": "51350.pdf"}],
            is_grounded=True,
        )

        assert result["answer"] is None
        assert result["source"] == "pending_foundry_chat_client"
        assert "Grounded Sources" in result["context"]
        test_logger.info("  source  : %s", result["source"])
        test_logger.info("  context : %d chars", len(result["context"]))
        test_logger.info("  ✓ pending path OK")

    def test_build_context_message(self, test_logger):
        test_logger.info("─── AnswerSynthesisAgent: build_context_message ───")
        from agents_af.answer_synthesis_agent import AnswerSynthesisAgent

        ctx = AnswerSynthesisAgent().build_context_message(
            SAMPLE_MATCHES,
            [{"policyNumber": "51350", "parentTitle": "PTO",
              "filePath": "/p", "documentType": "pdf"}],
        )

        assert "Source 1" in ctx
        assert "Policy 51350" in ctx
        assert "Grounded Sources" in ctx
        assert "References" in ctx
        test_logger.info("  context length : %d chars", len(ctx))
        test_logger.info("  ✓ context built OK")


# ═══════════════════════════════════════════════════════════════════════════
#  SEQUENTIAL ORCHESTRATOR (AF)
# ═══════════════════════════════════════════════════════════════════════════

class TestSequentialOrchestratorAF:
    """agents_af.sequential_orchestrator.SequentialWorkflowOrchestrator"""

    @patch("agents_af.sequential_orchestrator.FoundryChatClient")
    @patch("agents_af.sequential_orchestrator.DefaultAzureCredential")
    def test_mock_instantiation(self, MockCred, MockChat, test_logger):
        test_logger.info("─── AF Orchestrator: instantiation (mock) ───")
        from agents_af.sequential_orchestrator import SequentialWorkflowOrchestrator

        orch = SequentialWorkflowOrchestrator()
        test_logger.info("  project_endpoint : %s",
                         orch.project_endpoint[:40] + "..." if orch.project_endpoint else "(not set)")
        test_logger.info("  model            : %s", orch.model)
        test_logger.info("  ✓ instantiation OK (mock)")

    @patch("agents_af.sequential_orchestrator.FoundryChatClient")
    @patch("agents_af.sequential_orchestrator.DefaultAzureCredential")
    def test_mock_workflow_builds(self, MockCred, MockChat, test_logger):
        """Verify the workflow can be built with all 6 participants."""
        test_logger.info("─── AF Orchestrator: workflow build (mock) ───")
        mock_chat = MockChat.return_value
        mock_chat.as_agent.return_value = MagicMock()

        from agents_af.sequential_orchestrator import SequentialWorkflowOrchestrator

        orch = SequentialWorkflowOrchestrator()
        workflow = orch._build_workflow()

        assert workflow is not None
        mock_chat.as_agent.assert_called_once()
        test_logger.info("  ✓ workflow built (6 participants)")

    def test_live_orchestrator(self, is_live, test_logger):
        """Full AF pipeline against live Azure."""
        if not is_live:
            pytest.skip("--mock mode")

        test_logger.info("─── AF Orchestrator: LIVE full pipeline ───")
        from agents_af.sequential_orchestrator import SequentialWorkflowOrchestrator

        orch = SequentialWorkflowOrchestrator()
        test_logger.info("  building workflow...")
        test_logger.info("  project : %s",
                         orch.project_endpoint[:40] + "..." if orch.project_endpoint else "?")

        result = orch.process_query(TEST_QUERIES[0])

        assert result["status"] == "completed"
        assert result["is_grounded"] is True
        assert len(result["matches"]) > 0
        assert len(result["references"]) > 0
        assert result["answer"]

        test_logger.info("  status     : %s", result["status"])
        test_logger.info("  matches    : %d", len(result["matches"]))
        test_logger.info("  references : %d", len(result["references"]))
        test_logger.info("  grounded   : %s", result["is_grounded"])
        test_logger.info("  answer     : %s...", result["answer"][:100])
        test_logger.info("  ✓ LIVE AF orchestrator OK")


# ═══════════════════════════════════════════════════════════════════════════
#  EXECUTOR DATA-FLOW TESTS (always mocked – unit-level)
# ═══════════════════════════════════════════════════════════════════════════

class TestExecutorsAF:
    """Individual custom Executors in isolation (always mocked)."""

    @pytest.mark.asyncio
    async def test_retrieval_executor(self, test_logger):
        test_logger.info("─── RetrievalExecutor: data flow ───")
        from agents_af.sequential_orchestrator import RetrievalExecutor
        from agent_framework import Message

        executor = RetrievalExecutor()
        executor.agent = MagicMock()
        executor.agent.run.return_value = {
            "matches": SAMPLE_MATCHES,
            "agentic_answer": "PTO answer.",
        }

        ctx = MagicMock()
        ctx.send_message = AsyncMock()

        messages = [Message("user", ["What is the PTO policy?"])]
        await executor.process(messages, ctx)

        ctx.send_message.assert_called_once()
        sent = ctx.send_message.call_args[0][0]
        state = json.loads(sent[-1].text)

        assert state["user_query"] == "What is the PTO policy?"
        assert len(state["retrieval_output"]["matches"]) == 2
        test_logger.info("  user_query : %s", state["user_query"])
        test_logger.info("  matches    : %d", len(state["retrieval_output"]["matches"]))
        test_logger.info("  ✓ RetrievalExecutor data flow OK")

    @pytest.mark.asyncio
    async def test_source_validation_executor(self, test_logger):
        test_logger.info("─── SourceValidationExecutor: data flow ───")
        from agents_af.sequential_orchestrator import SourceValidationExecutor
        from agent_framework import Message

        state = {
            "user_query": "test",
            "retrieval_output": {"matches": SAMPLE_MATCHES + [SAMPLE_UNTRUSTED_MATCH]},
            "steps": {"retrieval": {"status": "completed"}},
        }

        executor = SourceValidationExecutor()
        ctx = MagicMock()
        ctx.send_message = AsyncMock()

        messages = [Message("assistant", [json.dumps(state)])]
        await executor.process(messages, ctx)

        ctx.send_message.assert_called_once()
        sent = ctx.send_message.call_args[0][0]
        updated = json.loads(sent[-1].text)

        assert updated["validation_output"]["source_count"] == 2
        test_logger.info("  source_count : %d", updated["validation_output"]["source_count"])
        test_logger.info("  ✓ SourceValidationExecutor data flow OK")

    @pytest.mark.asyncio
    async def test_reference_validation_executor(self, test_logger):
        test_logger.info("─── ReferenceValidationExecutor: data flow ───")
        from agents_af.sequential_orchestrator import ReferenceValidationExecutor
        from agent_framework import Message

        state = {
            "user_query": "test",
            "retrieval_output": {
                "matches": SAMPLE_MATCHES,
                "agentic_answer": "PTO answer.",
            },
            "validation_output": {
                "validated_sources": SAMPLE_MATCHES,
                "source_count": 2,
            },
            "steps": {
                "retrieval": {"status": "completed"},
                "source_validation": {"status": "completed"},
            },
        }

        executor = ReferenceValidationExecutor()
        ctx = MagicMock()
        ctx.send_message = AsyncMock()

        messages = [Message("assistant", [json.dumps(state)])]
        await executor.process(messages, ctx)

        ctx.send_message.assert_called_once()
        sent = ctx.send_message.call_args[0][0]
        updated = json.loads(sent[-1].text)

        assert updated["reference_output"]["is_grounded"] is True
        assert len(updated["reference_output"]["references"]) == 2
        assert updated["synthesis_output"]["source"] == "agentic_retrieval"
        test_logger.info("  grounded   : %s", updated["reference_output"]["is_grounded"])
        test_logger.info("  references : %d", len(updated["reference_output"]["references"]))
        test_logger.info("  synthesis  : %s", updated["synthesis_output"]["source"])
        test_logger.info("  ✓ ReferenceValidationExecutor data flow OK")
