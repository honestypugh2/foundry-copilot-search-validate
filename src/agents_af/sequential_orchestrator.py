"""
Sequential Workflow Orchestrator (Agent Framework + FoundryChatClient)

Uses Agent Framework's SequentialBuilder to coordinate the HR policy
knowledge retrieval and validation pipeline.  Mixes FoundryChatClient
Agents with custom Executors for deterministic steps.

Architecture:
    FoundryChatClient  →  Agent Framework SequentialBuilder
        Step 1  →  RetrievalExecutor         (custom Executor — deterministic)
        Step 2  →  SourceValidationExecutor  (custom Executor — deterministic)
        Step 3  →  ReferenceValidationExecutor (custom Executor — deterministic)
        Step 4  →  AnswerSynthesisAgent      (FoundryChatClient Agent via AF)

References:
    Agent Framework + FoundryChatClient:
        https://learn.microsoft.com/en-us/agent-framework/agents/providers/microsoft-foundry
    Sequential Workflow + Custom Executors:
        https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/sequential
"""

import json
import logging
import os
from typing import Any, Dict, List, Optional, cast

from agent_framework import (
    Agent,
    AgentExecutorResponse,
    Executor,
    Message,
    WorkflowContext,
    handler,
)
from agent_framework.foundry import FoundryChatClient
from agent_framework.orchestrations import SequentialBuilder
from azure.identity import DefaultAzureCredential

from agents_af.retrieval_agent import RetrievalAgent
from agents_af.source_validator_agent import SourceValidatorAgent
from agents_af.reference_validator_agent import ReferenceValidatorAgent
from agents_af.answer_synthesis_agent import AnswerSynthesisAgent
from observability import enable_tracing

logger = logging.getLogger(__name__)

# Activate Azure Monitor / OpenTelemetry tracing once when this module is
# loaded so spans flow to App Insights (and thus Foundry portal Tracing).
enable_tracing()

# Agent name used for both AF wrapper and Foundry portal registration
FOUNDRY_AGENT_NAME = "hr-answer-synthesis"

# All pipeline agents to register in Foundry Portal for tracking
PIPELINE_AGENTS = {
    "hr-retrieval-agent": {
        "instructions": (
            "Agentic Retrieval executor. Decomposes user queries into sub-queries, "
            "executes them against the Azure AI Search knowledge base (hr-knowledge-base), "
            "reranks results, and returns grounded matches with activity traces."
        ),
    },
    "hr-source-validator": {
        "instructions": (
            "Source Validation executor. Filters retrieval matches to only those from "
            "the trusted 'ask-hr-knowledge' blob container, ensuring document provenance."
        ),
    },
    "hr-reference-validator": {
        "instructions": (
            "Reference Validation executor. Verifies that cited file paths and policy "
            "numbers exist in validated sources, ensuring grounding integrity."
        ),
    },
    "hr-answer-synthesis": {
        "instructions": (
            "Answer Synthesis agent (FoundryChatClient). Synthesizes a final grounded "
            "answer from validated sources and references, citing policy numbers and "
            "file paths. Uses agentic retrieval passthrough when available."
        ),
    },
}


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class WorkflowState(dict):
    """Complete workflow state passed between executors."""
    pass


# ---------------------------------------------------------------------------
# Step 1: Retrieval Executor (custom — deterministic / agentic retrieval)
# ---------------------------------------------------------------------------

class RetrievalExecutor(Executor):
    """
    Custom Executor wrapping the RetrievalAgent.

    Receives the user query, performs agentic retrieval or hybrid search,
    and forwards matches to the next executor.
    """

    def __init__(self):
        self.agent = RetrievalAgent()
        super().__init__(id="retrieval")

    @handler
    async def process(
        self,
        messages: List[Message],
        ctx: WorkflowContext[List[Message]],
    ) -> None:
        """Execute agentic retrieval / hybrid search and forward results."""
        user_query = messages[-1].text.strip()
        logger.info("Step 1: Retrieval Agent")

        retrieval_output = self.agent.run(user_query)

        logger.info(
            "✓ Retrieval complete: %d matches", len(retrieval_output["matches"])
        )

        state = WorkflowState(
            {
                "user_query": user_query,
                "retrieval_output": retrieval_output,
                "steps": {
                    "retrieval": {
                        "status": "completed",
                        "match_count": len(retrieval_output["matches"]),
                    }
                },
            }
        )

        state_json = json.dumps(state, default=str)
        await ctx.send_message(
            messages + [Message("assistant", [state_json], author_name="retrieval")]
        )


# ---------------------------------------------------------------------------
# Step 2: Source Validation Executor (custom — deterministic)
# ---------------------------------------------------------------------------

class SourceValidationExecutor(Executor):
    """
    Custom Executor wrapping the SourceValidatorAgent.

    Filters matches to those from the trusted ask-hr-knowledge container.
    """

    def __init__(self):
        self.agent = SourceValidatorAgent()
        super().__init__(id="source_validation")

    @handler
    async def process(
        self,
        messages: List[Message],
        ctx: WorkflowContext[List[Message]],
    ) -> None:
        """Validate source provenance and forward results."""
        state = WorkflowState(json.loads(messages[-1].text))
        logger.info("Step 2: Source Validation Agent")

        validation_output = self.agent.run(state["retrieval_output"])

        logger.info(
            "✓ Source validation complete: %d/%d sources trusted",
            validation_output["source_count"],
            len(state["retrieval_output"]["matches"]),
        )

        state["validation_output"] = validation_output
        state["steps"]["source_validation"] = {
            "status": "completed",
            "source_count": validation_output["source_count"],
        }

        state_json = json.dumps(state, default=str)
        await ctx.send_message(
            messages
            + [Message("assistant", [state_json], author_name="source_validation")]
        )


# ---------------------------------------------------------------------------
# Step 3: Reference Validation Executor (custom — deterministic)
# ---------------------------------------------------------------------------

class ReferenceValidationExecutor(Executor):
    """
    Custom Executor wrapping the ReferenceValidatorAgent.

    Ensures citations and file paths exist, then prepares state
    for answer synthesis.
    """

    def __init__(self):
        self.agent = ReferenceValidatorAgent()
        super().__init__(id="reference_validation")

    @handler
    async def process(
        self,
        messages: List[Message],
        ctx: WorkflowContext[List[Message]],
    ) -> None:
        """Validate references and forward to answer synthesis."""
        state = WorkflowState(json.loads(messages[-1].text))
        logger.info("Step 3: Reference Validation Agent")

        reference_output = self.agent.run(state["validation_output"])

        logger.info(
            "✓ Reference validation complete: grounded=%s, references=%d",
            reference_output["is_grounded"],
            len(reference_output["references"]),
        )

        state["reference_output"] = reference_output
        state["steps"]["reference_validation"] = {
            "status": "completed",
            "is_grounded": reference_output["is_grounded"],
            "reference_count": len(reference_output["references"]),
        }

        # Check if agentic answer is available — if so, use it directly
        agentic_answer = state["retrieval_output"].get("agentic_answer")
        synthesis_agent = AnswerSynthesisAgent()

        synthesis_output = synthesis_agent.run(
            user_query=state["user_query"],
            validated_sources=state["validation_output"]["validated_sources"],
            references=reference_output["references"],
            is_grounded=reference_output["is_grounded"],
            agentic_answer=agentic_answer,
        )

        state["synthesis_output"] = synthesis_output
        state["steps"]["answer_synthesis_check"] = {
            "status": "completed",
            "has_agentic_answer": bool(agentic_answer),
        }

        state_json = json.dumps(state, default=str)
        await ctx.send_message(
            messages
            + [
                Message(
                    "assistant",
                    [state_json],
                    author_name="reference_validation",
                )
            ]
        )


# ---------------------------------------------------------------------------
# Step 4: Answer Synthesis Executor (custom — routes to Agent or passthrough)
# ---------------------------------------------------------------------------

class AnswerSynthesisExecutor(Executor):
    """
    Custom Executor for final answer synthesis.

    If the agentic retrieval answer was already captured, passes it
    through.  Otherwise, invokes the FoundryChatClient Agent (Step 5
    in the pipeline) via message forwarding.

    This executor yields the final workflow output.
    """

    def __init__(self):
        super().__init__(id="answer_synthesis")

    @handler
    async def process(
        self,
        messages: List[Message],
        ctx: WorkflowContext[List[Message], Dict[str, Any]],
    ) -> None:
        """Synthesize answer and yield final workflow output."""
        state = WorkflowState(json.loads(messages[-1].text))
        logger.info("Step 4: Answer Synthesis")

        synthesis_output = state.get("synthesis_output", {})
        answer = synthesis_output.get("answer")

        if answer is not None:
            # Agentic answer or pre-computed answer available
            logger.info(
                "✓ Answer synthesis complete (source=%s)",
                synthesis_output.get("source", "unknown"),
            )
        else:
            # No agentic answer — this will be handled by the
            # FoundryChatClient Agent that follows in the pipeline
            logger.info("No agentic answer — forwarding to FoundryChatClient Agent")
            # Build the prompt for the FoundryChatClient Agent
            context = synthesis_output.get("context", "")
            prompt = f"Question: {state['user_query']}\n\n{context}"

            state["pending_synthesis_prompt"] = prompt
            state_json = json.dumps(state, default=str)
            await ctx.send_message(
                messages
                + [
                    Message(
                        "assistant",
                        [state_json],
                        author_name="answer_synthesis",
                    )
                ]
            )
            return

        # Build final results
        final_results = {
            "status": "completed",
            "user_query": state["user_query"],
            "answer": answer,
            "matches": state["retrieval_output"]["matches"],
            "validated_sources": state["validation_output"]["validated_sources"],
            "references": state["reference_output"]["references"],
            "is_grounded": state["reference_output"]["is_grounded"],
            "activity": state["retrieval_output"].get("activity", []),
            "steps": state["steps"],
        }

        state_json = json.dumps(state, default=str)
        await ctx.send_message(
            messages
            + [
                Message(
                    "assistant",
                    [state_json],
                    author_name="answer_synthesis",
                )
            ]
        )
        await ctx.yield_output(final_results)


# ---------------------------------------------------------------------------
# Step 5: Final Synthesis Executor (captures FoundryChatClient Agent output)
# ---------------------------------------------------------------------------

class FinalSynthesisExecutor(Executor):
    """
    Terminal executor that captures the FoundryChatClient Agent's
    response when LLM synthesis was needed, or passes through the
    already-complete output.

    This handles both paths:
    - Agentic answer available → extract final_results from state
    - FoundryChatClient Agent answered → extract answer from agent response
    """

    def __init__(self):
        super().__init__(id="final_synthesis")

    @handler
    async def process(
        self,
        agent_response: AgentExecutorResponse,
        ctx: WorkflowContext[List[Message], Dict[str, Any]],
    ) -> None:
        """Extract final results from agent response or state."""
        conversation = agent_response.full_conversation
        if not conversation:
            await ctx.yield_output({"status": "failed", "error": "No conversation"})
            return

        # Try to find the last state JSON from our executors
        state = None
        agent_answer = None

        for msg in reversed(list(conversation)):
            if msg.role == "assistant" and msg.text:
                # Check if it's a state JSON
                try:
                    parsed = json.loads(msg.text)
                    if isinstance(parsed, dict) and "user_query" in parsed:
                        # Prefer the most complete state (has the most keys)
                        if state is None or len(parsed) > len(state):
                            state = WorkflowState(parsed)
                        continue
                except (json.JSONDecodeError, TypeError):
                    pass
                # Otherwise it's the FoundryChatClient Agent's answer
                if agent_answer is None:
                    agent_answer = msg.text

        if state is None:
            await ctx.yield_output(
                {"status": "failed", "error": "Could not extract workflow state"}
            )
            return

        # Determine the answer
        synthesis_output = state.get("synthesis_output", {})
        answer = synthesis_output.get("answer")

        if answer is None and agent_answer:
            # FoundryChatClient Agent provided the answer
            answer = agent_answer
            logger.info("✓ Answer synthesized by FoundryChatClient Agent")
        elif answer is None:
            answer = "The agent did not produce a response."

        # Use .get() with safe defaults — earlier state snapshots may
        # not contain all keys if the FoundryChatClient Agent's message
        # appeared before the final executor state was written.
        retrieval_output = state.get("retrieval_output", {})
        validation_output = state.get("validation_output", {})
        reference_output = state.get("reference_output", {})

        final_results = {
            "status": "completed",
            "user_query": state.get("user_query", ""),
            "answer": answer,
            "matches": retrieval_output.get("matches", []),
            "validated_sources": validation_output.get("validated_sources", []),
            "references": reference_output.get("references", []),
            "is_grounded": reference_output.get("is_grounded", False),
            "activity": retrieval_output.get("activity", []),
            "steps": state.get("steps", {}),
        }

        await ctx.send_message(
            list(conversation)
            + [
                Message(
                    "assistant",
                    [json.dumps(final_results, default=str)],
                    author_name="final_synthesis",
                )
            ]
        )
        await ctx.yield_output(final_results)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

class SequentialWorkflowOrchestrator:
    """
    Sequential Workflow Orchestrator using Agent Framework + FoundryChatClient.

    Coordinates the HR policy knowledge pipeline through a sequential
    chain mixing custom Executors (deterministic steps) with a
    FoundryChatClient Agent (LLM synthesis).

    Architecture:
        RetrievalExecutor (custom) → SourceValidationExecutor (custom)
        → ReferenceValidationExecutor (custom) → AnswerSynthesisExecutor (custom)
        → FoundryChatClient Agent (answer synthesis) → FinalSynthesisExecutor (custom)

    When agentic retrieval provides a synthesized answer, the
    FoundryChatClient Agent step is effectively a no-op and the
    answer passes through.
    """

    def __init__(self):
        self.project_endpoint = (
            os.getenv("AZURE_AI_FOUNDRY_PROJECT_ENDPOINT")
            or os.getenv("AZURE_AI_PROJECT_ENDPOINT")
            or os.getenv("FOUNDRY_PROJECT_ENDPOINT", "")
        )
        self.model = os.getenv("FOUNDRY_MODEL") or os.getenv(
            "AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4.1"
        )
        self.search_index = os.getenv(
            "AZURE_SEARCH_INDEX_NAME", "hr_lab_index"
        )
        logger.info("SequentialWorkflowOrchestrator (AF) initialised")

    # ------------------------------------------------------------------
    # FoundryChatClient Agent
    # ------------------------------------------------------------------

    def _create_foundry_agent(self) -> Agent:
        """Create a FoundryChatClient-backed Agent for answer synthesis.

        Also registers the agent in Azure AI Foundry portal via
        AIProjectClient so it is visible for monitoring and management.
        """
        credential = DefaultAzureCredential()

        chat_client = FoundryChatClient(
            project_endpoint=self.project_endpoint,
            model=self.model,
            credential=credential,
        )

        synthesis_agent = AnswerSynthesisAgent()
        instructions = (
            synthesis_agent.answer_instructions
            if synthesis_agent.answer_instructions
            else synthesis_agent.INSTRUCTIONS
        )

        # Register in Foundry portal so the agent is visible there
        self._register_agent_in_portal(
            credential=credential,
            instructions=instructions,
        )

        return chat_client.as_agent(
            instructions=instructions,
            name=FOUNDRY_AGENT_NAME,
        )

    def _register_agent_in_portal(
        self,
        credential: DefaultAzureCredential,
        instructions: str,
    ) -> None:
        """Register all pipeline agents in Azure AI Foundry portal."""
        try:
            from azure.ai.projects import AIProjectClient
            from azure.ai.projects.models import PromptAgentDefinition

            pc = AIProjectClient(
                endpoint=self.project_endpoint,
                credential=credential,
            )

            # Register all pipeline agents for portal visibility
            for agent_name, agent_config in PIPELINE_AGENTS.items():
                agent_instructions = agent_config["instructions"]
                # For the answer synthesis agent, use the full instructions
                if agent_name == FOUNDRY_AGENT_NAME:
                    agent_instructions = instructions

                try:
                    existing = pc.agents.get(agent_name=agent_name)
                    logger.info(
                        "Agent '%s' already in Foundry portal (version=%s)",
                        existing.name,
                        existing.versions.get("latest", {}).get("version", "unknown") if existing.versions else "unknown",
                    )
                    continue
                except Exception:
                    pass

                try:
                    agent = pc.agents.create_version(
                        agent_name=agent_name,
                        definition=PromptAgentDefinition(
                            model=self.model,
                            instructions=agent_instructions,
                            temperature=0.0,
                        ),
                    )
                    logger.info(
                        "Agent '%s' registered in Foundry portal (version=%s)",
                        agent.name,
                        agent.version,
                    )
                except Exception as e:
                    logger.warning(
                        "Could not register agent '%s' in Foundry portal: %s",
                        agent_name, e,
                    )

        except Exception as e:
            logger.warning(
                "Could not register agents in Foundry portal: %s", e
            )

    # ------------------------------------------------------------------
    # Workflow builder
    # ------------------------------------------------------------------

    def _build_workflow(self) -> Any:
        """
        Build the sequential workflow mixing custom Executors with
        a FoundryChatClient Agent.

        Pipeline:
            RetrievalExecutor → SourceValidationExecutor
            → ReferenceValidationExecutor → AnswerSynthesisExecutor
            → FoundryChatClient Agent → FinalSynthesisExecutor
        """
        retrieval_executor = RetrievalExecutor()
        source_validation_executor = SourceValidationExecutor()
        reference_validation_executor = ReferenceValidationExecutor()
        answer_synthesis_executor = AnswerSynthesisExecutor()
        foundry_agent = self._create_foundry_agent()
        final_synthesis_executor = FinalSynthesisExecutor()

        workflow = SequentialBuilder(
            participants=[
                retrieval_executor,
                source_validation_executor,
                reference_validation_executor,
                answer_synthesis_executor,
                foundry_agent,
                final_synthesis_executor,
            ]
        ).build()

        return workflow

    # ------------------------------------------------------------------
    # Async entry-point
    # ------------------------------------------------------------------

    async def process_query_async(self, user_query: str) -> Dict[str, Any]:
        """
        Process a user query through the sequential workflow.

        Args:
            user_query: Natural-language HR policy question.

        Returns:
            Complete workflow results including matches, validated
            sources, references, and grounding status.
        """
        logger.info("Starting AF sequential workflow for query: %r", user_query)

        try:
            workflow = self._build_workflow()
            output_data = None

            async for event in workflow.run(user_query, stream=True):
                if event.type == "status":
                    logger.debug("Workflow state: %s", event.data)

                elif event.type == "output":
                    output_data = event.data
                    logger.info("Workflow output received")

                elif event.type == "executor_failed":
                    details = event.data
                    logger.error(
                        "Executor failed: %s - %s: %s",
                        getattr(details, "executor_id", "unknown"),
                        getattr(details, "error_type", "Error"),
                        getattr(details, "message", str(details)),
                    )
                    raise Exception(
                        f"Executor failed: {getattr(details, 'message', str(details))}"
                    )

                elif event.type == "failed":
                    details = event.data
                    logger.error(
                        "Workflow failed: %s: %s",
                        getattr(details, "error_type", "Error"),
                        getattr(details, "message", str(details)),
                    )
                    raise Exception(
                        f"Workflow failed: {getattr(details, 'message', str(details))}"
                    )

            # Extract final results from output event
            if output_data is not None:
                if isinstance(output_data, dict):
                    return output_data
                if isinstance(output_data, list):
                    for msg in reversed(output_data):
                        if hasattr(msg, "text") and msg.text:
                            try:
                                return json.loads(msg.text)
                            except (json.JSONDecodeError, TypeError):
                                continue
                    raise Exception(
                        "Could not extract results from workflow output messages"
                    )
                return output_data
            else:
                raise Exception("No workflow output received")

        except Exception as e:
            logger.error("✗ Workflow failed: %s", e)
            raise

    # ------------------------------------------------------------------
    # Sync wrapper
    # ------------------------------------------------------------------

    def process_query(self, user_query: str) -> Dict[str, Any]:
        """
        Synchronous wrapper around process_query_async.

        Args:
            user_query: Natural-language HR policy question.

        Returns:
            Complete workflow results.
        """
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop and loop.is_running():
            import nest_asyncio

            nest_asyncio.apply()
            return loop.run_until_complete(self.process_query_async(user_query))
        else:
            # Re-use a single event loop to avoid "Event loop is closed"
            # errors from httpx AsyncClient cleanup tasks.
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                return loop.run_until_complete(self.process_query_async(user_query))
            finally:
                # Shut down async generators and executors but keep the
                # loop open so pending httpx cleanup tasks can finish.
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.run_until_complete(loop.shutdown_default_executor())
