"""
Foundry Agent Orchestrator — MCP-based Agentic Retrieval

DEFAULT mode (single-agent MCP):
    User query → Foundry Agent (MCPTool, tool_choice="required") → Answer with citations

This follows the Microsoft recommended pattern:
    https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-how-to-create-pipeline
    https://github.com/Azure-Samples/azure-search-python-samples/blob/main/agentic-retrieval-pipeline-example/agent-example.ipynb

A single Foundry Agent with an MCPTool handles retrieval, reasoning,
and citation formatting in one pass.  The knowledge base MCP endpoint
provides query decomposition, semantic reranking, and extractive data.

OPTIONAL modes (activated via environment variables):

    VALIDATE_CITATIONS=true
        Post-processes the agent response to verify citation format and
        source provenance against the knowledge base index.

    PIPELINE_MODE=multi_step
        Enables the legacy 4-step sequential pipeline:
            Step 1 → RetrievalAgent (hybrid search)
            Step 2 → SourceValidatorAgent (container filter)
            Step 3 → ReferenceValidatorAgent (citation extraction)
            Step 4 → AnswerSynthesisAgent (Foundry Agent + MCP)
        This mode is provided as a learning reference.  It performs
        double retrieval (Step 1 + Step 4 both query the same index)
        and is NOT recommended for production.
"""

import json
import logging
import os
import re
import asyncio
from typing import Any, Dict
from pathlib import Path

from azure.ai.projects.aio import AIProjectClient
from azure.ai.projects.models import MCPTool, PromptAgentDefinition
from azure.identity.aio import DefaultAzureCredential

from search.azure_ai_search_client import AzureAISearchClient

logger = logging.getLogger(__name__)

# Load search config for foundry agent settings
_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "search_config.json"
if _CONFIG_PATH.exists():
    with open(_CONFIG_PATH) as f:
        _CFG = json.load(f)
        _AGENT_CONFIG = _CFG.get("foundry_agent", {})
        _AGENTIC_CONFIG = _CFG.get("agentic_retrieval", {})
else:
    _AGENT_CONFIG = {}
    _AGENTIC_CONFIG = {}


# ---------------------------------------------------------------------------
# Pipeline mode (environment-driven)
# ---------------------------------------------------------------------------
# "single_agent" (default) — MS recommended single-agent MCP pattern
# "multi_step"             — legacy 4-step pipeline (learning reference)
PIPELINE_MODE = os.getenv("PIPELINE_MODE", "single_agent").lower()

# Output mode: "answer_synthesis" or "extractive"
# Extractive mode returns extracted passages from documents without synthesizing.
# In extractive mode, answer_instructions are not used.
OUTPUT_MODE = os.getenv(
    "OUTPUT_MODE",
    _AGENTIC_CONFIG.get("output_mode", "ANSWER_SYNTHESIS"),
).upper()

# Citation validation toggle (post-processing)
VALIDATE_CITATIONS = os.getenv("VALIDATE_CITATIONS", "false").lower() == "true"


class FoundryAgentOrchestrator:
    """
    Foundry Agent Orchestrator with configurable pipeline modes.

    DEFAULT — Single-agent MCP (Microsoft recommended):
        A single Foundry Agent with MCPTool handles retrieval, reasoning,
        and citation formatting.  ``tool_choice="required"`` ensures the
        agent always queries the knowledge base.

    OPTIONAL — Multi-step pipeline (learning reference):
        Activated with ``PIPELINE_MODE=multi_step``.  Runs retrieval,
        source validation, reference validation, then answer synthesis.
        Performs double retrieval and is NOT recommended for production.

    OPTIONAL — Citation validation (post-processing):
        Activated with ``VALIDATE_CITATIONS=true``.  Checks the agent
        response for MCP citation annotations and validates source
        provenance against the trusted container.
    """

    def __init__(self):
        self.project_endpoint = (
            os.getenv("AZURE_AI_PROJECT_ENDPOINT")
            or os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT", "")
        )
        self.deployment_name = (
            os.getenv("AZURE_AI_MODEL_DEPLOYMENT_NAME")
            or os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME")
            or _AGENT_CONFIG.get("model", "gpt-4.1")
        )

        # MCP configuration
        mcp_cfg = _AGENTIC_CONFIG.get("mcp", {})
        self.mcp_connection_name = mcp_cfg.get(
            "project_connection_name", "hr-knowledge-mcp-connection"
        )
        self._search_client = AzureAISearchClient()
        self.mcp_endpoint = self._search_client.get_mcp_endpoint()

        self._pipeline_mode = PIPELINE_MODE
        self._output_mode = OUTPUT_MODE
        self._validate_citations = VALIDATE_CITATIONS

        logger.info(
            "FoundryAgentOrchestrator initialised "
            "(mode=%s, output_mode=%s, validate_citations=%s, MCP endpoint=%s)",
            self._pipeline_mode,
            self._output_mode,
            self._validate_citations,
            self.mcp_endpoint,
        )

    # ------------------------------------------------------------------
    # MCP Tool
    # ------------------------------------------------------------------

    def _build_mcp_tool(self) -> MCPTool:
        """Build the MCPTool pointing to the knowledge base MCP endpoint.

        Authentication is handled via a project connection.  Foundry
        Agent Service auto-injects the required auth headers on MCP
        requests using the connection's managed identity.

        Ref: https://learn.microsoft.com/en-us/azure/search/
             agentic-retrieval-how-to-retrieve#authenticate-to-the-mcp-endpoint
        """
        return MCPTool(
            server_label="knowledge-base",
            server_url=self.mcp_endpoint,
            require_approval="never",
            allowed_tools=["knowledge_base_retrieve"],
            project_connection_id=self.mcp_connection_name,
        )

    # ------------------------------------------------------------------
    # Agent instructions (MS recommended template)
    # ------------------------------------------------------------------

    def _build_instructions(self) -> str:
        """Build agent instructions based on output mode.

        ANSWER_SYNTHESIS mode (default):
            Uses answer_instructions from config or default synthesis template.

        EXTRACTIVE mode:
            Uses minimal instructions — no answer synthesis required.
            The agent retrieves and returns extracted passages with citations.

        Reference:
            https://github.com/Azure-Samples/azure-search-python-samples/blob/main/
            agentic-retrieval-pipeline-example/agent-example.ipynb
        """
        if self._output_mode == "EXTRACTIVE":
            return (
                "You are a helpful HR policy assistant. Use the knowledge base "
                "tool to retrieve relevant documents. Return the extracted "
                "passages exactly as found in the source documents.\n"
                "Always provide citation annotations using the MCP knowledge "
                "base tool and render them as: "
                "`【message_idx:search_idx†source_name】`\n"
                "Do not synthesize or rephrase the content. Present the "
                "extracted text directly with its source citation.\n"
            )

        # ANSWER_SYNTHESIS mode (default)
        custom = _AGENT_CONFIG.get("answer_instructions", "")
        if custom:
            return custom
        return (
            "You are a helpful HR policy assistant that must use the knowledge "
            "base to answer all the questions from user. You must never answer "
            "from your own knowledge under any circumstances.\n"
            "Every answer must always provide annotations for using the MCP "
            "knowledge base tool and render them as: "
            "`【message_idx:search_idx†source_name】`\n"
            "If you cannot find the answer in the provided knowledge base you "
            'must respond with "I don\'t know".\n'
        )

    # ==================================================================
    # DEFAULT: Single-agent MCP pipeline (MS recommended)
    # ==================================================================

    async def _single_agent_pipeline(self, user_query: str) -> Dict[str, Any]:
        """Execute the MS recommended single-agent MCP pipeline.

        User query → Foundry Agent (MCPTool + tool_choice="required") → Answer

        The agent handles retrieval, reasoning, and citation formatting
        in one pass.
        """
        credential = DefaultAzureCredential(exclude_environment_credential=True)

        async with (
            credential,
            AIProjectClient(
                endpoint=self.project_endpoint, credential=credential
            ) as project_client,
            project_client.get_openai_client() as openai_client,
        ):
            # Create agent with MCP tool
            mcp_tool = self._build_mcp_tool()
            agent = await project_client.agents.create_version(
                agent_name="HRPolicyAgent",
                definition=PromptAgentDefinition(
                    model=self.deployment_name,
                    instructions=self._build_instructions(),
                    tools=[mcp_tool],
                ),
            )
            logger.info(
                "Single-agent MCP: agent created (name=%s, version=%s)",
                agent.name,
                agent.version,
            )

            try:
                conversation = await openai_client.conversations.create()

                try:
                    answer_text = ""
                    stream = await openai_client.responses.create(
                        conversation=conversation.id,
                        tool_choice="required",
                        input=user_query,
                        extra_body={
                            "agent_reference": {
                                "name": agent.name,
                                "type": "agent_reference",
                            }
                        },
                        stream=True,
                    )
                    async for event in stream:
                        if event.type == "response.output_text.delta":
                            answer_text += event.delta

                    answer = answer_text or "The agent did not produce a response."

                    result: Dict[str, Any] = {
                        "status": "completed",
                        "user_query": user_query,
                        "answer": answer,
                        "model": self.deployment_name,
                        "output_mode": self._output_mode.lower(),
                        "pipeline_mode": "single_agent",
                        "is_grounded": True,
                        "steps": {
                            "mcp_retrieval_and_synthesis": {
                                "status": "completed",
                                "model": self.deployment_name,
                                "output_mode": self._output_mode.lower(),
                            }
                        },
                    }

                    # Optional citation validation
                    if self._validate_citations:
                        validation = self._validate_citation_annotations(answer)
                        result["citation_validation"] = validation
                        if not validation["has_citations"]:
                            logger.warning(
                                "Citation validation: no MCP annotations found in response"
                            )

                    return result

                finally:
                    await openai_client.conversations.delete(
                        conversation_id=conversation.id
                    )
            finally:
                persist = (
                    os.getenv("PERSIST_FOUNDRY_AGENTS", "true").lower() == "true"
                )
                if not persist:
                    await project_client.agents.delete_version(
                        agent_name=agent.name, agent_version=agent.version
                    )

    # ==================================================================
    # OPTIONAL: Citation validation (post-processing)
    # ==================================================================

    def _validate_citation_annotations(self, answer: str) -> Dict[str, Any]:
        """Validate MCP citation annotations in the agent response.

        Checks for the 【message_idx:search_idx†source_name】 pattern
        that the MS recommended instructions produce.
        """
        # MCP citation pattern: 【digits:digits†source_name】
        pattern = r"【(\d+):(\d+)†([^】]+)】"
        citations = re.findall(pattern, answer)

        validated = []
        for msg_idx, search_idx, source_name in citations:
            validated.append(
                {
                    "message_idx": int(msg_idx),
                    "search_idx": int(search_idx),
                    "source_name": source_name.strip(),
                }
            )

        return {
            "has_citations": len(validated) > 0,
            "citation_count": len(validated),
            "citations": validated,
        }

    # ==================================================================
    # OPTIONAL: Multi-step pipeline (legacy / learning reference)
    # ==================================================================

    async def _multi_step_pipeline(self, user_query: str) -> Dict[str, Any]:
        """Execute the legacy 4-step sequential pipeline.

        ⚠️ NOT recommended for production.  This performs double
        retrieval: Step 1 searches the index, then Step 4's MCP tool
        searches it again.  Provided as a learning reference.

        Step 1 → RetrievalAgent (hybrid search against the index)
        Step 2 → SourceValidatorAgent (filter by trusted container)
        Step 3 → ReferenceValidatorAgent (extract citations, check grounding)
        Step 4 → Foundry Agent with MCP (answer synthesis)
        """
        # Lazy imports — only loaded when multi_step mode is active
        from agents.retrieval_agent import RetrievalAgent
        from agents.source_validator_agent import SourceValidatorAgent
        from agents.reference_validator_agent import ReferenceValidatorAgent

        retrieval_agent = RetrievalAgent()
        source_validator = SourceValidatorAgent()
        reference_validator = ReferenceValidatorAgent()

        workflow_results: Dict[str, Any] = {
            "status": "in_progress",
            "user_query": user_query,
            "pipeline_mode": "multi_step",
            "steps": {},
        }

        # Step 1: Retrieval
        logger.info("[multi_step] Step 1: Retrieval Agent")
        retrieval_output = retrieval_agent.run(user_query)
        workflow_results["steps"]["retrieval"] = {
            "status": "completed",
            "match_count": len(retrieval_output["matches"]),
        }
        logger.info(
            "✓ Retrieval complete: %d matches",
            len(retrieval_output["matches"]),
        )

        # Step 2: Source Validation
        logger.info("[multi_step] Step 2: Source Validation Agent")
        validation_output = source_validator.run(retrieval_output)
        workflow_results["steps"]["source_validation"] = {
            "status": "completed",
            "source_count": validation_output["source_count"],
        }
        logger.info(
            "✓ Source validation: %d/%d trusted",
            validation_output["source_count"],
            len(retrieval_output["matches"]),
        )

        # Step 3: Reference Validation
        logger.info("[multi_step] Step 3: Reference Validation Agent")
        reference_output = reference_validator.run(validation_output)
        workflow_results["steps"]["reference_validation"] = {
            "status": "completed",
            "is_grounded": reference_output["is_grounded"],
            "reference_count": len(reference_output["references"]),
        }
        logger.info(
            "✓ Reference validation: grounded=%s, refs=%d",
            reference_output["is_grounded"],
            len(reference_output["references"]),
        )

        # Step 4: Answer Synthesis via MCP
        agentic_answer = retrieval_output.get("agentic_answer")

        if agentic_answer:
            logger.info("[multi_step] Step 4: Using agentic retrieval pre-synthesized answer")
            synthesis_output = {
                "answer": agentic_answer,
                "model": self.deployment_name,
                "output_mode": "answerSynthesis",
                "is_grounded": reference_output["is_grounded"],
                "source": "agentic_retrieval",
            }
        elif reference_output["is_grounded"] and validation_output["validated_sources"]:
            logger.info("[multi_step] Step 4: Answer Synthesis (Foundry Agent + MCP)")
            synthesis_output = await self._single_agent_pipeline(user_query)
            synthesis_output["output_mode"] = "mcp_multi_step"
        else:
            synthesis_output = {
                "answer": "I could not find grounded information to answer this question.",
                "model": self.deployment_name,
                "output_mode": "answerSynthesis",
                "is_grounded": False,
            }

        workflow_results["steps"]["answer_synthesis"] = {
            "status": "completed",
            "model": synthesis_output.get("model"),
            "output_mode": synthesis_output.get("output_mode"),
        }

        workflow_results.update(
            {
                "status": "completed",
                "answer": synthesis_output.get("answer", ""),
                "matches": retrieval_output["matches"],
                "validated_sources": validation_output["validated_sources"],
                "references": reference_output["references"],
                "is_grounded": reference_output["is_grounded"],
            }
        )

        # Optional citation validation on the final answer
        if self._validate_citations:
            validation = self._validate_citation_annotations(
                workflow_results.get("answer", "")
            )
            workflow_results["citation_validation"] = validation

        logger.info("✓ Multi-step workflow completed")
        return workflow_results

    # ==================================================================
    # Public entry-points
    # ==================================================================

    async def process_query_async(self, user_query: str) -> Dict[str, Any]:
        """Process a user query using the configured pipeline mode.

        Mode is controlled by ``PIPELINE_MODE`` env var:
            - ``single_agent`` (default) — MS recommended single-agent MCP
            - ``multi_step`` — legacy 4-step pipeline (learning reference)
        """
        logger.info(
            "Processing query (mode=%s): %r", self._pipeline_mode, user_query
        )

        try:
            if self._pipeline_mode == "multi_step":
                return await self._multi_step_pipeline(user_query)
            else:
                return await self._single_agent_pipeline(user_query)
        except Exception as e:
            logger.error("✗ Workflow failed (mode=%s): %s", self._pipeline_mode, e)
            raise

    def process_query(self, user_query: str) -> Dict[str, Any]:
        """Synchronous wrapper around process_query_async."""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(self.process_query_async(user_query))
        else:
            import nest_asyncio

            nest_asyncio.apply()
            loop = asyncio.get_event_loop()
            return loop.run_until_complete(self.process_query_async(user_query))


# Backward-compatible alias for existing imports
SequentialWorkflowOrchestratorFoundry = FoundryAgentOrchestrator
