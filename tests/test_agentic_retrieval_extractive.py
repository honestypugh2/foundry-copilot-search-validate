"""
Tests – Agentic Retrieval with output_mode EXTRACTIVE

Validates that the agents/ agentic retrieval pipeline returns extracted
passages (not synthesized answers) when output_mode is set to EXTRACTIVE.

Dual-mode: connects to Azure by default, mocked with --mock flag.

Usage:
    # Live (default) – hits real Azure AI Search & Foundry
    PYTHONPATH=$PWD/src pytest tests/test_agentic_retrieval_extractive.py -v -s

    # Mock – no Azure needed
    PYTHONPATH=$PWD/src pytest tests/test_agentic_retrieval_extractive.py -v -s --mock
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

logger = logging.getLogger("test.agentic_retrieval_extractive")

from conftest import SAMPLE_MATCHES
from test_queries import TEST_QUERIES, QUERY_EXPECTATIONS


# ═══════════════════════════════════════════════════════════════════════════
#  SAMPLE DATA – Agentic Retrieval EXTRACTIVE responses
# ═══════════════════════════════════════════════════════════════════════════

SAMPLE_AGENTIC_EXTRACTIVE_RESPONSE = {
    "response": "",  # EXTRACTIVE mode returns no synthesized answer
    "matches": [
        {
            "content": "PTO is accrued at 1.5 days per month for full-time employees.",
            "filePath": "https://storage.blob.core.windows.net/ask-hr-knowledge/51350.pdf",
            "fileName": "51350 - Types of Leave_ Paid Time Off (PTO) (23472_2).docx",
            "parentTitle": "51350 - Types of Leave_ Paid Time Off (PTO) (23472_2).docx",
            "policyNumber": "51350",
            "documentType": "",
            "container": "ask-hr-knowledge",
            "reranker_score": 3.85,
            "blob_url": "https://storage.blob.core.windows.net/ask-hr-knowledge/51350.pdf",
            "ref_id": "ref-001",
        },
        {
            "content": "Employees are eligible for PTO after completing 90 days of employment.",
            "filePath": "https://storage.blob.core.windows.net/ask-hr-knowledge/51350.pdf",
            "fileName": "51350 - Types of Leave_ Paid Time Off (PTO) (23472_2).docx",
            "parentTitle": "51350 - Types of Leave_ Paid Time Off (PTO) (23472_2).docx",
            "policyNumber": "51350",
            "documentType": "",
            "container": "ask-hr-knowledge",
            "reranker_score": 3.72,
            "blob_url": "https://storage.blob.core.windows.net/ask-hr-knowledge/51350.pdf",
            "ref_id": "ref-002",
        },
    ],
    "activity": [
        {"type": "query_rewrite", "query": "PTO policy accrual"},
        {"type": "search", "knowledge_source": "hr-knowledge-source"},
    ],
}

SAMPLE_EXTRACTIVE_AGENT_RESPONSE = (
    "PTO is accrued at 1.5 days per month for full-time employees. "
    "【0:0†51350 - Types of Leave_ Paid Time Off (PTO) (23472_2).docx】\n\n"
    "Employees are eligible for PTO after completing 90 days of employment. "
    "【0:1†51350 - Types of Leave_ Paid Time Off (PTO) (23472_2).docx】"
)


# ═══════════════════════════════════════════════════════════════════════════
#  RETRIEVAL AGENT – EXTRACTIVE MODE
# ═══════════════════════════════════════════════════════════════════════════

class TestRetrievalAgentExtractive:
    """Test RetrievalAgent with agentic retrieval in EXTRACTIVE output mode."""

    def test_agentic_retrieve_returns_matches_without_synthesis(self, test_logger):
        """EXTRACTIVE mode: agentic_retrieve returns matches with empty response."""
        test_logger.info("─── RetrievalAgent: EXTRACTIVE agentic retrieval (mock) ───")

        with patch("agents.retrieval_agent.AzureAISearchClient") as MockClient:
            mock_instance = MockClient.return_value
            mock_instance.agentic_retrieve.return_value = SAMPLE_AGENTIC_EXTRACTIVE_RESPONSE

            from agents.retrieval_agent import RetrievalAgent

            agent = RetrievalAgent()
            agent.search_client = mock_instance
            agent._agentic_enabled = True

            result = agent.run("What is the PTO policy?")

        assert "matches" in result
        assert len(result["matches"]) == 2
        # EXTRACTIVE: response text should be empty or minimal
        assert result.get("agentic_answer", "") == ""
        # Matches should contain extracted content
        assert result["matches"][0]["content"] != ""
        assert result["matches"][0]["policyNumber"] == "51350"
        test_logger.info("  matches     : %d", len(result["matches"]))
        test_logger.info("  answer      : (empty – extractive mode)")
        test_logger.info("  ✓ EXTRACTIVE agentic retrieval OK (mock)")

    def test_extractive_matches_have_required_fields(self, test_logger):
        """EXTRACTIVE matches contain all required metadata fields."""
        test_logger.info("─── RetrievalAgent: EXTRACTIVE field validation (mock) ───")

        with patch("agents.retrieval_agent.AzureAISearchClient") as MockClient:
            mock_instance = MockClient.return_value
            mock_instance.agentic_retrieve.return_value = SAMPLE_AGENTIC_EXTRACTIVE_RESPONSE

            from agents.retrieval_agent import RetrievalAgent

            agent = RetrievalAgent()
            agent.search_client = mock_instance
            agent._agentic_enabled = True

            result = agent.run("What is the PTO policy?")

        required_fields = [
            "content", "filePath", "fileName", "parentTitle",
            "policyNumber", "container", "reranker_score", "blob_url", "ref_id",
        ]
        for match in result["matches"]:
            for field in required_fields:
                assert field in match, f"Missing field: {field}"
            # All matches from EXTRACTIVE should have content (extracted passages)
            assert match["content"], "EXTRACTIVE match must have non-empty content"
            assert match["container"] == "ask-hr-knowledge"

        test_logger.info("  ✓ All required fields present in EXTRACTIVE matches")

    def test_extractive_activity_contains_query_plan(self, test_logger):
        """EXTRACTIVE mode returns activity (query decomposition/search steps)."""
        test_logger.info("─── RetrievalAgent: EXTRACTIVE activity log (mock) ───")

        with patch("agents.retrieval_agent.AzureAISearchClient") as MockClient:
            mock_instance = MockClient.return_value
            mock_instance.agentic_retrieve.return_value = SAMPLE_AGENTIC_EXTRACTIVE_RESPONSE

            from agents.retrieval_agent import RetrievalAgent

            agent = RetrievalAgent()
            agent.search_client = mock_instance
            agent._agentic_enabled = True

            result = agent.run("What is the PTO policy?")

        assert "activity" in result
        assert len(result["activity"]) > 0
        test_logger.info("  activity_steps : %d", len(result["activity"]))
        test_logger.info("  ✓ EXTRACTIVE activity log present")

    def test_extractive_fallback_to_hybrid_when_agentic_disabled(self, test_logger):
        """When agentic retrieval is disabled, falls back to hybrid search."""
        test_logger.info("─── RetrievalAgent: EXTRACTIVE fallback to hybrid (mock) ───")

        with patch("agents.retrieval_agent.AzureAISearchClient") as MockClient:
            mock_instance = MockClient.return_value
            mock_instance.hybrid_search.return_value = SAMPLE_MATCHES

            from agents.retrieval_agent import RetrievalAgent

            agent = RetrievalAgent()
            agent.search_client = mock_instance
            agent._agentic_enabled = False

            result = agent.run("What is the PTO policy?")

        assert "matches" in result
        assert len(result["matches"]) == 2
        # No agentic_answer in fallback mode
        assert "agentic_answer" not in result
        test_logger.info("  ✓ Fallback to hybrid search OK")

    def test_extractive_fallback_on_agentic_exception(self, test_logger):
        """Graceful fallback when agentic retrieval raises an exception."""
        test_logger.info("─── RetrievalAgent: EXTRACTIVE exception fallback (mock) ───")

        with patch("agents.retrieval_agent.AzureAISearchClient") as MockClient:
            mock_instance = MockClient.return_value
            mock_instance.agentic_retrieve.side_effect = RuntimeError("SDK unavailable")
            mock_instance.hybrid_search.return_value = SAMPLE_MATCHES

            from agents.retrieval_agent import RetrievalAgent

            agent = RetrievalAgent()
            agent.search_client = mock_instance
            agent._agentic_enabled = True

            result = agent.run("What is the PTO policy?")

        # Should fall back gracefully
        assert "matches" in result
        assert len(result["matches"]) > 0
        test_logger.info("  ✓ Graceful fallback on agentic exception")


# ═══════════════════════════════════════════════════════════════════════════
#  ORCHESTRATOR – EXTRACTIVE OUTPUT MODE
# ═══════════════════════════════════════════════════════════════════════════

class TestFoundryOrchestratorExtractive:
    """Test FoundryAgentOrchestrator with OUTPUT_MODE=EXTRACTIVE."""

    def test_extractive_instructions_no_synthesis(self, test_logger):
        """EXTRACTIVE mode instructions tell agent to return passages as-is."""
        test_logger.info("─── Orchestrator: EXTRACTIVE instructions ───")

        with patch.dict(os.environ, {
            "OUTPUT_MODE": "EXTRACTIVE",
            "AZURE_AI_PROJECT_ENDPOINT": "https://test.endpoint.com",
        }):
            with patch(
                "agents.sequential_orchestrator_foundry.AzureAISearchClient"
            ) as MockSearch:
                mock_search = MockSearch.return_value
                mock_search.get_mcp_endpoint.return_value = "https://mcp.test.com"

                # Re-import to pick up env var changes
                import importlib
                import agents.sequential_orchestrator_foundry as orch_module
                importlib.reload(orch_module)

                orchestrator = orch_module.FoundryAgentOrchestrator()
                instructions = orchestrator._build_instructions()

        assert "Do not synthesize" in instructions
        assert "extracted" in instructions.lower()
        assert "citation annotations" in instructions.lower()
        test_logger.info("  instructions: %s...", instructions[:80])
        test_logger.info("  ✓ EXTRACTIVE instructions validated")

    def test_extractive_output_mode_set(self, test_logger):
        """Orchestrator correctly reads OUTPUT_MODE=EXTRACTIVE from config."""
        test_logger.info("─── Orchestrator: EXTRACTIVE output mode detection ───")

        with patch.dict(os.environ, {
            "OUTPUT_MODE": "EXTRACTIVE",
            "AZURE_AI_PROJECT_ENDPOINT": "https://test.endpoint.com",
        }):
            with patch(
                "agents.sequential_orchestrator_foundry.AzureAISearchClient"
            ) as MockSearch:
                mock_search = MockSearch.return_value
                mock_search.get_mcp_endpoint.return_value = "https://mcp.test.com"

                import importlib
                import agents.sequential_orchestrator_foundry as orch_module
                importlib.reload(orch_module)

                orchestrator = orch_module.FoundryAgentOrchestrator()

        assert orchestrator._output_mode == "EXTRACTIVE"
        test_logger.info("  output_mode : %s", orchestrator._output_mode)
        test_logger.info("  ✓ EXTRACTIVE mode detected")

    @pytest.mark.asyncio
    async def test_extractive_single_agent_pipeline_mock(self, test_logger):
        """EXTRACTIVE single-agent pipeline returns extracted passages with citations."""
        test_logger.info("─── Orchestrator: EXTRACTIVE single-agent pipeline (mock) ───")

        with patch.dict(os.environ, {
            "OUTPUT_MODE": "EXTRACTIVE",
            "AZURE_AI_PROJECT_ENDPOINT": "https://test.endpoint.com",
            "PERSIST_FOUNDRY_AGENTS": "false",
        }):
            with patch(
                "agents.sequential_orchestrator_foundry.AzureAISearchClient"
            ) as MockSearch:
                mock_search = MockSearch.return_value
                mock_search.get_mcp_endpoint.return_value = "https://mcp.test.com"

                import importlib
                import agents.sequential_orchestrator_foundry as orch_module
                importlib.reload(orch_module)

                orchestrator = orch_module.FoundryAgentOrchestrator()

            # Mock the entire async pipeline
            mock_event = MagicMock(type="response.output_text.delta", delta=SAMPLE_EXTRACTIVE_AGENT_RESPONSE)

            class MockStream:
                def __aiter__(self):
                    return self

                async def __anext__(self):
                    if not hasattr(self, "_done"):
                        self._done = True
                        return mock_event
                    raise StopAsyncIteration

            mock_stream = MockStream()

            mock_conversation = MagicMock()
            mock_conversation.id = "conv-123"

            mock_openai = AsyncMock()
            mock_openai.conversations.create = AsyncMock(return_value=mock_conversation)
            mock_openai.conversations.delete = AsyncMock()
            mock_openai.responses.create = AsyncMock(return_value=mock_stream)

            mock_agent = MagicMock()
            mock_agent.name = "HRPolicyAgent"
            mock_agent.version = "v1"

            mock_project = AsyncMock()
            mock_project.agents.create_version = AsyncMock(return_value=mock_agent)
            mock_project.agents.delete_version = AsyncMock()
            mock_project.get_openai_client = MagicMock(return_value=mock_openai)

            # Mock context managers
            mock_openai.__aenter__ = AsyncMock(return_value=mock_openai)
            mock_openai.__aexit__ = AsyncMock(return_value=False)
            mock_project.__aenter__ = AsyncMock(return_value=mock_project)
            mock_project.__aexit__ = AsyncMock(return_value=False)

            mock_credential = AsyncMock()
            mock_credential.__aenter__ = AsyncMock(return_value=mock_credential)
            mock_credential.__aexit__ = AsyncMock(return_value=False)

            with patch.object(orch_module, "DefaultAzureCredential", return_value=mock_credential):
                with patch.object(orch_module, "AIProjectClient", return_value=mock_project):
                    result = await orchestrator.process_query_async("What is the PTO policy?")

        assert result["status"] == "completed"
        assert result["output_mode"] == "extractive"
        assert "【" in result["answer"]  # MCP citations present
        assert "51350" in result["answer"]
        test_logger.info("  status      : %s", result["status"])
        test_logger.info("  output_mode : %s", result["output_mode"])
        test_logger.info("  has_citations: True")
        test_logger.info("  ✓ EXTRACTIVE single-agent pipeline OK (mock)")

    def test_extractive_citation_validation(self, test_logger):
        """Citation validation detects MCP annotations in EXTRACTIVE output."""
        test_logger.info("─── Orchestrator: EXTRACTIVE citation validation ───")

        with patch.dict(os.environ, {
            "OUTPUT_MODE": "EXTRACTIVE",
            "VALIDATE_CITATIONS": "true",
            "AZURE_AI_PROJECT_ENDPOINT": "https://test.endpoint.com",
        }):
            with patch(
                "agents.sequential_orchestrator_foundry.AzureAISearchClient"
            ) as MockSearch:
                mock_search = MockSearch.return_value
                mock_search.get_mcp_endpoint.return_value = "https://mcp.test.com"

                import importlib
                import agents.sequential_orchestrator_foundry as orch_module
                importlib.reload(orch_module)

                orchestrator = orch_module.FoundryAgentOrchestrator()

        validation = orchestrator._validate_citation_annotations(
            SAMPLE_EXTRACTIVE_AGENT_RESPONSE
        )

        assert validation["has_citations"] is True
        assert validation["citation_count"] == 2
        assert validation["citations"][0]["source_name"] == (
            "51350 - Types of Leave_ Paid Time Off (PTO) (23472_2).docx"
        )
        test_logger.info("  citations: %d", validation["citation_count"])
        test_logger.info("  ✓ EXTRACTIVE citation validation OK")

    def test_extractive_no_answer_instructions_used(self, test_logger):
        """In EXTRACTIVE mode, answer_instructions from config are NOT used."""
        test_logger.info("─── Orchestrator: EXTRACTIVE skips answer_instructions ───")

        with patch.dict(os.environ, {
            "OUTPUT_MODE": "EXTRACTIVE",
            "AZURE_AI_PROJECT_ENDPOINT": "https://test.endpoint.com",
        }):
            with patch(
                "agents.sequential_orchestrator_foundry.AzureAISearchClient"
            ) as MockSearch:
                mock_search = MockSearch.return_value
                mock_search.get_mcp_endpoint.return_value = "https://mcp.test.com"

                import importlib
                import agents.sequential_orchestrator_foundry as orch_module
                importlib.reload(orch_module)

                orchestrator = orch_module.FoundryAgentOrchestrator()
                instructions = orchestrator._build_instructions()

        # EXTRACTIVE should NOT contain answer_instructions content
        assert "Provide grounded evidence" not in instructions
        assert "Do not synthesize" in instructions
        test_logger.info("  ✓ answer_instructions correctly excluded in EXTRACTIVE mode")


# ═══════════════════════════════════════════════════════════════════════════
#  AGENTIC RETRIEVAL CLIENT – EXTRACTIVE OUTPUT
# ═══════════════════════════════════════════════════════════════════════════

class TestAgenticRetrievalClientExtractive:
    """Test AzureAISearchClient.agentic_retrieve in EXTRACTIVE context."""

    def test_agentic_retrieve_returns_references_as_matches(self, test_logger):
        """agentic_retrieve normalizes KB references into match dicts."""
        test_logger.info("─── SearchClient: agentic_retrieve reference parsing ───")

        # Mock the KnowledgeBaseRetrievalClient response
        mock_ref = MagicMock()
        mock_ref.as_dict.return_value = {
            "id": "ref-001",
            "reranker_score": 3.85,
            "source_data": {
                "snippet": "PTO is accrued at 1.5 days per month.",
                "metadata_storage_path": "https://storage.blob.core.windows.net/ask-hr-knowledge/51350.pdf",
                "metadata_storage_name": "51350 - Types of Leave_ Paid Time Off (PTO) (23472_2).docx",
                "parent_title": "51350 - Types of Leave_ Paid Time Off (PTO) (23472_2).docx",
                "policy_number": "51350",
                "blob_url": "https://storage.blob.core.windows.net/ask-hr-knowledge/51350.pdf",
            },
        }

        mock_activity = MagicMock()
        mock_activity.as_dict.return_value = {
            "type": "search",
            "knowledge_source": "hr-knowledge-source",
        }

        mock_result = MagicMock()
        mock_result.response = []  # EXTRACTIVE: empty response
        mock_result.references = [mock_ref]
        mock_result.activity = [mock_activity]

        with patch("search.azure_ai_search_client.AGENTIC_RETRIEVAL_AVAILABLE", True):
            with patch(
                "search.azure_ai_search_client.KnowledgeBaseRetrievalClient"
            ) as MockKB:
                mock_kb_instance = MockKB.return_value
                mock_kb_instance.retrieve.return_value = mock_result

                from search.azure_ai_search_client import AzureAISearchClient

                with patch.dict(os.environ, {"AZURE_SEARCH_ENDPOINT": "https://search.test.com"}):
                    client = AzureAISearchClient()
                    result = client.agentic_retrieve(
                        [{"role": "user", "content": "What is the PTO policy?"}]
                    )

        assert result["response"] == ""
        assert len(result["matches"]) == 1
        match = result["matches"][0]
        assert match["content"] == "PTO is accrued at 1.5 days per month."
        assert match["policyNumber"] == "51350"
        assert match["container"] == "ask-hr-knowledge"
        assert match["reranker_score"] == 3.85
        assert match["ref_id"] == "ref-001"
        test_logger.info("  ✓ agentic_retrieve reference parsing OK (EXTRACTIVE)")

    def test_agentic_retrieve_handles_multiple_references(self, test_logger):
        """Multiple references are all normalized into matches."""
        test_logger.info("─── SearchClient: multiple EXTRACTIVE references ───")

        mock_refs = []
        for i, (snippet, policy) in enumerate([
            ("PTO accrual rules", "51350"),
            ("Dress code requirements", "52005"),
            ("Holiday pay schedule", "50715"),
        ]):
            ref = MagicMock()
            ref.as_dict.return_value = {
                "id": f"ref-{i:03d}",
                "reranker_score": 3.5 - (i * 0.1),
                "source_data": {
                    "snippet": snippet,
                    "metadata_storage_path": f"https://storage.blob.core.windows.net/ask-hr-knowledge/{policy}.pdf",
                    "metadata_storage_name": f"{policy} - Policy.docx",
                    "parent_title": f"{policy} - Policy.docx",
                    "policy_number": policy,
                    "blob_url": f"https://storage.blob.core.windows.net/ask-hr-knowledge/{policy}.pdf",
                },
            }
            mock_refs.append(ref)

        mock_result = MagicMock()
        mock_result.response = []
        mock_result.references = mock_refs
        mock_result.activity = []

        with patch("search.azure_ai_search_client.AGENTIC_RETRIEVAL_AVAILABLE", True):
            with patch(
                "search.azure_ai_search_client.KnowledgeBaseRetrievalClient"
            ) as MockKB:
                mock_kb_instance = MockKB.return_value
                mock_kb_instance.retrieve.return_value = mock_result

                from search.azure_ai_search_client import AzureAISearchClient

                with patch.dict(os.environ, {"AZURE_SEARCH_ENDPOINT": "https://search.test.com"}):
                    client = AzureAISearchClient()
                    result = client.agentic_retrieve(
                        [{"role": "user", "content": "HR policies overview"}]
                    )

        assert len(result["matches"]) == 3
        policies = [m["policyNumber"] for m in result["matches"]]
        assert "51350" in policies
        assert "52005" in policies
        assert "50715" in policies
        test_logger.info("  matches: %d policies", len(result["matches"]))
        test_logger.info("  ✓ Multiple EXTRACTIVE references OK")

    def test_agentic_retrieve_empty_references(self, test_logger):
        """Handles case where no references are returned."""
        test_logger.info("─── SearchClient: EXTRACTIVE with no references ───")

        mock_result = MagicMock()
        mock_result.response = []
        mock_result.references = None
        mock_result.activity = []

        with patch("search.azure_ai_search_client.AGENTIC_RETRIEVAL_AVAILABLE", True):
            with patch(
                "search.azure_ai_search_client.KnowledgeBaseRetrievalClient"
            ) as MockKB:
                mock_kb_instance = MockKB.return_value
                mock_kb_instance.retrieve.return_value = mock_result

                from search.azure_ai_search_client import AzureAISearchClient

                with patch.dict(os.environ, {"AZURE_SEARCH_ENDPOINT": "https://search.test.com"}):
                    client = AzureAISearchClient()
                    result = client.agentic_retrieve(
                        [{"role": "user", "content": "nonexistent topic xyz"}]
                    )

        assert result["response"] == ""
        assert result["matches"] == []
        assert result["activity"] == []
        test_logger.info("  ✓ Empty EXTRACTIVE references handled gracefully")


# ═══════════════════════════════════════════════════════════════════════════
#  LIVE TESTS – EXTRACTIVE AGENTIC RETRIEVAL
# ═══════════════════════════════════════════════════════════════════════════

class TestAgenticRetrievalExtractiveLive:
    """Live tests for agentic retrieval in EXTRACTIVE mode.

    Skipped in --mock mode. Requires Azure credentials and
    AZURE_SEARCH_ENDPOINT / AZURE_AI_PROJECT_ENDPOINT env vars.
    """

    def test_live_extractive_retrieval(self, is_live, test_logger):
        """Live: agentic retrieval returns extracted passages."""
        if not is_live:
            pytest.skip("--mock mode")

        test_logger.info("─── LIVE: EXTRACTIVE agentic retrieval ───")
        from agents.retrieval_agent import RetrievalAgent

        agent = RetrievalAgent()
        if not agent._agentic_enabled:
            pytest.skip("Agentic retrieval not available")

        result = agent.run("What is the PTO policy?")

        assert "matches" in result
        assert len(result["matches"]) > 0
        # Verify matches contain extracted content
        for match in result["matches"]:
            assert match["content"], "EXTRACTIVE match must have content"
            assert match["container"] == "ask-hr-knowledge"
        test_logger.info("  matches: %d", len(result["matches"]))
        test_logger.info("  ✓ LIVE EXTRACTIVE retrieval OK")

    def test_live_extractive_policy_lookup(self, is_live, test_logger):
        """Live: EXTRACTIVE retrieval finds correct policy by number."""
        if not is_live:
            pytest.skip("--mock mode")

        test_logger.info("─── LIVE: EXTRACTIVE policy lookup ───")
        from agents.retrieval_agent import RetrievalAgent

        agent = RetrievalAgent()
        if not agent._agentic_enabled:
            pytest.skip("Agentic retrieval not available")

        query = QUERY_EXPECTATIONS[0]  # Policy 51350 PTO
        result = agent.run(query.query)

        assert "matches" in result
        assert len(result["matches"]) > 0

        # Check that expected policy appears in results
        found_policies = [m.get("policyNumber") for m in result["matches"]]
        assert query.expected_policy in found_policies, (
            f"Expected policy {query.expected_policy} not in results: {found_policies}"
        )
        test_logger.info("  query   : %s", query.query)
        test_logger.info("  found   : %s", found_policies)
        test_logger.info("  ✓ LIVE EXTRACTIVE policy lookup OK")

    def test_live_extractive_metadata_fields(self, is_live, test_logger):
        """Live: EXTRACTIVE matches include required metadata."""
        if not is_live:
            pytest.skip("--mock mode")

        test_logger.info("─── LIVE: EXTRACTIVE metadata validation ───")
        from agents.retrieval_agent import RetrievalAgent

        agent = RetrievalAgent()
        if not agent._agentic_enabled:
            pytest.skip("Agentic retrieval not available")

        result = agent.run("Find Policy 52005 for the Uniform Dress Code")

        assert len(result["matches"]) > 0
        match = result["matches"][0]

        # Required metadata for EXTRACTIVE mode
        assert match.get("filePath"), "filePath must be present"
        assert match.get("fileName"), "fileName must be present"
        assert match.get("container") == "ask-hr-knowledge"
        test_logger.info("  fileName  : %s", match.get("fileName"))
        test_logger.info("  filePath  : %s", match.get("filePath", "")[:60])
        test_logger.info("  ✓ LIVE EXTRACTIVE metadata OK")

    @pytest.mark.asyncio
    async def test_live_extractive_orchestrator_pipeline(self, is_live, test_logger):
        """Live: Full orchestrator pipeline in EXTRACTIVE mode."""
        if not is_live:
            pytest.skip("--mock mode")

        test_logger.info("─── LIVE: EXTRACTIVE orchestrator pipeline ───")

        with patch.dict(os.environ, {"OUTPUT_MODE": "EXTRACTIVE"}):
            import importlib
            import agents.sequential_orchestrator_foundry as orch_module
            importlib.reload(orch_module)

            orchestrator = orch_module.FoundryAgentOrchestrator()
            if not orchestrator.project_endpoint:
                pytest.skip("AZURE_AI_PROJECT_ENDPOINT not set")

            result = await orchestrator.process_query_async("What is the PTO policy?")

        assert result["status"] == "completed"
        assert result["output_mode"] == "extractive"
        assert result["answer"], "EXTRACTIVE pipeline should return extracted content"
        test_logger.info("  status      : %s", result["status"])
        test_logger.info("  output_mode : %s", result["output_mode"])
        test_logger.info("  answer      : %s...", result["answer"][:80])
        test_logger.info("  ✓ LIVE EXTRACTIVE orchestrator pipeline OK")

    def test_live_extractive_multiple_queries(self, is_live, test_logger):
        """Live: Run multiple queries in EXTRACTIVE mode and validate results."""
        if not is_live:
            pytest.skip("--mock mode")

        test_logger.info("─── LIVE: EXTRACTIVE batch query validation ───")
        from agents.retrieval_agent import RetrievalAgent

        agent = RetrievalAgent()
        if not agent._agentic_enabled:
            pytest.skip("Agentic retrieval not available")

        results = []
        for qe in QUERY_EXPECTATIONS[:5]:
            result = agent.run(qe.query)
            found_policies = [m.get("policyNumber") for m in result["matches"]]
            hit = qe.expected_policy in found_policies if qe.expected_policy else True
            results.append({
                "query": qe.query,
                "expected_policy": qe.expected_policy,
                "found_policies": found_policies,
                "hit": hit,
                "match_count": len(result["matches"]),
            })
            test_logger.info(
                "  [%s] %s → %d matches, hit=%s",
                qe.expected_policy or "n/a",
                qe.description,
                len(result["matches"]),
                hit,
            )

        hits = sum(1 for r in results if r["hit"])
        test_logger.info("  Hit rate: %d / %d", hits, len(results))
        # At least 60% of queries should find expected policy
        assert hits >= len(results) * 0.6, (
            f"EXTRACTIVE hit rate too low: {hits}/{len(results)}"
        )
        test_logger.info("  ✓ LIVE EXTRACTIVE batch queries OK")
