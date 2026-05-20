"""
Foundry Agent Orchestrator — Pattern B: Custom Metadata-Lookup Tool
(Recommended for Production)

A dedicated Foundry Agent tool handles file-location queries by performing
a targeted Azure AI Search query that returns only metadata fields, bypassing
the knowledge base entirely for those queries.

Architecture:
    User: "Where is Policy 51350?"
        ↓
    Foundry Agent (tool_choice routing)
        ├─ knowledge_base_retrieve  → content/policy questions (MCP)
        └─ file_metadata_lookup     → file-location questions (custom tool)
            ↓
        Azure AI Search direct query
            (select: metadata_storage_path, metadata_storage_name, blob_url)
            ↓
        Deterministic JSON response → Agent formats for user

This pattern provides DETERMINISTIC file paths — no LLM hallucination
risk for metadata fields — while keeping full KB retrieval for content.

Environment:
    ORCHESTRATOR_PATTERN=B   (selects this orchestrator)
    PIPELINE_MODE=single_agent (default)

Microsoft Reference Implementations:
    - Azure-Samples/azure-search-openai-demo — Production-grade RAG with
      agentic retrieval (PR #2835: Knowledge Base support)
      Docs: docs/agentic_retrieval.md
      Uses USE_AGENTIC_KNOWLEDGEBASE=true environment variable pattern
      Multiple knowledge source routing (web, SharePoint, index)
    - Azure/azure-search-vector-samples — Vector search patterns including
      hybrid retrieval with metadata fields

Our Reference Implementation:
    This repo: foundry-copilot-search-validate — Full end-to-end agentic
    retrieval with MCP, file-location instruction injection, and test harness
"""

import json
import logging
import os
import re
import asyncio
from typing import Any, Dict
from pathlib import Path

from azure.ai.projects.aio import AIProjectClient
from azure.ai.projects.models import MCPTool, PromptAgentDefinition, FunctionTool
from azure.identity.aio import DefaultAzureCredential

from search.azure_ai_search_client import AzureAISearchClient

logger = logging.getLogger(__name__)

# Load config
_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "search_config.json"
if _CONFIG_PATH.exists():
    with open(_CONFIG_PATH) as f:
        _CFG = json.load(f)
        _AGENT_CONFIG = _CFG.get("foundry_agent", {})
        _AGENTIC_CONFIG = _CFG.get("agentic_retrieval", {})
else:
    _AGENT_CONFIG = {}
    _AGENTIC_CONFIG = {}


# Output mode
OUTPUT_MODE = os.getenv(
    "OUTPUT_MODE",
    _AGENTIC_CONFIG.get("output_mode", "EXTRACTIVE"),
).upper()

# Citation validation toggle
VALIDATE_CITATIONS = os.getenv("VALIDATE_CITATIONS", "false").lower() == "true"

# --------------------------------------------------------------------------
# Tool definitions
#
# Pattern B architecture:
#   knowledge_base_retrieve  → MCP tool (content/policy questions)
#   file_metadata_lookup     → Function tool (file-location questions)
#
# The Foundry Agent API does not transparently handle MCP tool calls when
# mixed with FunctionTool in the same agent. When the agent calls the MCP
# tool, we intercept it and execute agentic_retrieve() directly, then
# submit the result back as function_call_output. This gives us:
#   - The exact architecture the user described (MCP + custom tool)
#   - Direct access to KB activity data (subqueries, elapsed times)
#   - Deterministic file paths from the custom tool
# --------------------------------------------------------------------------
_FILE_METADATA_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "file_metadata_lookup",
        "description": (
            "Look up the storage location, file path, blob URL, and filename "
            "for a document in the HR policy knowledge base. Use this tool "
            "when the user asks WHERE a document is located, asks for a file "
            "path, URL, link, blob storage path, or document location. "
            "This performs a direct index search for metadata fields only — "
            "it does NOT use the knowledge base. "
            "Do NOT use this for content/policy questions."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "The search query to find the document. Include "
                        "policy number and/or document title."
                    ),
                },
            },
            "required": ["query"],
        },
    },
}


class PatternBOrchestrator:
    """
    Pattern B: Custom Metadata-Lookup Tool (Recommended for Production).

    Architecture:
        User query → Foundry Agent (tool_choice routing)
            ├─ knowledge_base_retrieve  → content/policy questions (MCP)
            └─ file_metadata_lookup     → file-location questions (custom tool)
                ↓
            Azure AI Search direct query
                (select: metadata_storage_path, metadata_storage_name, blob_url)
                ↓
            Deterministic JSON response → Agent formats for user

    The MCP tool is registered on the agent but the platform does not
    auto-handle it when mixed with a FunctionTool. We intercept MCP calls,
    execute agentic_retrieve() directly, and submit the result back. This
    preserves the architecture while giving us access to activity data.
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

        self._output_mode = OUTPUT_MODE
        self._validate_citations = VALIDATE_CITATIONS
        self._last_activity: list = []

        logger.info(
            "PatternBOrchestrator initialised "
            "(output_mode=%s, validate_citations=%s, MCP endpoint=%s)",
            self._output_mode,
            self._validate_citations,
            self.mcp_endpoint,
        )

    # ------------------------------------------------------------------
    # Tools
    # ------------------------------------------------------------------

    def _build_mcp_tool(self) -> MCPTool:
        """Build the MCPTool for knowledge_base_retrieve (content questions)."""
        return MCPTool(
            server_label="knowledge-base",
            server_url=self.mcp_endpoint,
            require_approval="never",
            allowed_tools=["knowledge_base_retrieve"],
            project_connection_id=self.mcp_connection_name,
        )

    def _build_function_tool(self) -> FunctionTool:
        """Build the file_metadata_lookup function tool (file-location questions)."""
        func_def = _FILE_METADATA_TOOL_DEFINITION["function"]
        return FunctionTool(
            name=func_def["name"],
            description=func_def["description"],
            parameters=func_def["parameters"],
            strict=False,
        )

    # ------------------------------------------------------------------
    # Tool implementations
    # ------------------------------------------------------------------

    def _execute_kb_retrieve(self, query: str) -> Dict[str, Any]:
        """
        Execute agentic retrieval against the HR knowledge base.

        Called when the agent invokes knowledge_base_retrieve (MCP tool).
        We intercept and execute via agentic_retrieve() directly, giving
        us access to activity/subquery data.
        """
        try:
            ar_result = self._search_client.agentic_retrieve(
                messages=[{"role": "user", "content": query}]
            )
            response_text = ar_result.get("response", "")
            matches = ar_result.get("matches", [])
            self._last_activity = ar_result.get("activity", [])

            # Format results for the agent
            documents = []
            for match in matches[:5]:
                content = match.get("content") or ""
                documents.append({
                    "content": content[:500],
                    "source_name": match.get("fileName") or "",
                    "metadata_storage_path": match.get("filePath") or "",
                    "blob_url": match.get("blob_url") or "",
                    "parent_title": match.get("parentTitle") or "",
                    "policy_number": match.get("policyNumber") or "",
                })

            return {
                "found": len(documents) > 0,
                "answer": response_text,
                "document_count": len(documents),
                "documents": documents,
            }
        except Exception as e:
            logger.error("knowledge_base_retrieve failed: %s", e)
            return {
                "found": False,
                "answer": "",
                "message": f"Knowledge base retrieval failed: {e}",
                "documents": [],
            }

    def file_metadata_lookup(self, query: str) -> Dict[str, Any]:
        """
        Perform a direct Azure AI Search query for document metadata only.

        Bypasses the knowledge base entirely. Returns deterministic results:
        metadata_storage_path, metadata_storage_name, blob_url, parent_title,
        policy_number. No LLM synthesis — exact field values from the index.

        This is the custom tool implementation referenced in:
        Azure-Samples/azure-search-openai-demo (docs/agentic_retrieval.md)
        """
        results = self._search_client.hybrid_search(query, top=3)

        if not results:
            return {
                "found": False,
                "message": f"No documents found matching: {query}",
                "documents": [],
            }

        documents = []
        for hit in results:
            documents.append({
                "metadata_storage_name": hit.get("fileName", ""),
                "metadata_storage_path": hit.get("filePath", ""),
                "blob_url": hit.get("blob_url", ""),
                "parent_title": hit.get("parentTitle", ""),
                "policy_number": hit.get("policyNumber", ""),
                "reranker_score": hit.get("reranker_score"),
            })

        return {
            "found": True,
            "document_count": len(documents),
            "documents": documents,
        }

    # ------------------------------------------------------------------
    # Agent instructions
    # ------------------------------------------------------------------

    def _build_instructions(self) -> str:
        """Build agent instructions for Pattern B (two tools)."""
        return (
            "You are a helpful HR policy assistant with TWO tools:\n\n"
            "1. **knowledge_base_retrieve** (MCP): Use this for questions "
            "about policy CONTENT — what a policy says, requirements, "
            "procedures, eligibility, etc.\n\n"
            "2. **file_metadata_lookup** (function): Use this when the "
            "user asks WHERE a document is located, wants a file path, URL, "
            "link, blob storage path, or document location. This performs a "
            "direct index search and returns exact metadata — it does NOT "
            "use the knowledge base.\n\n"
            "ROUTING RULES:\n"
            "- 'Where is...', 'file path for...', 'give me the link to...', "
            "'blob URL for...', 'storage path of...' → use file_metadata_lookup\n"
            "- 'What does...say', 'summarize...', 'what are the requirements...' "
            "→ use knowledge_base_retrieve\n"
            "- If the query asks BOTH content AND location, use BOTH tools.\n\n"
            "RESPONSE RULES:\n"
            "- For file-location results, present the EXACT values returned "
            "by file_metadata_lookup. Never modify paths or URLs.\n"
            "- For content results, always provide MCP citation annotations "
            "using the format: 【message_idx:search_idx†source_name】\n"
            "- If you cannot find the answer, respond with 'I don't know'.\n"
        )

    # ==================================================================
    # Pipeline execution
    # ==================================================================

    async def _execute_pipeline(self, user_query: str) -> Dict[str, Any]:
        """Execute the Pattern B dual-tool pipeline.

        User query → Agent (MCP + Function tool) → Answer
        """
        credential = DefaultAzureCredential(exclude_environment_credential=True)

        async with (
            credential,
            AIProjectClient(
                endpoint=self.project_endpoint, credential=credential
            ) as project_client,
            project_client.get_openai_client() as openai_client,
        ):
            # Create agent with BOTH tools
            mcp_tool = self._build_mcp_tool()
            func_tool = self._build_function_tool()

            agent = await project_client.agents.create_version(
                agent_name="HRPolicyAgentB",
                definition=PromptAgentDefinition(
                    model=self.deployment_name,
                    instructions=self._build_instructions(),
                    tools=[mcp_tool, func_tool],
                ),
            )
            logger.info(
                "Pattern B: agent created (name=%s, version=%s)",
                agent.name,
                agent.version,
            )

            try:
                conversation = await openai_client.conversations.create()

                try:
                    answer_text = ""
                    tool_calls_made = []
                    token_usage = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}

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

                    pending_function_call = None

                    async for event in stream:
                        # Capture text output
                        if event.type == "response.output_text.delta":
                            answer_text += event.delta

                        # Capture function tool calls
                        elif event.type == "response.function_call_arguments.done":
                            pending_function_call = {
                                "name": event.name,
                                "arguments": event.arguments,
                                "call_id": event.item_id,
                            }

                        # Handle function tool execution
                        elif event.type == "response.output_item.done":
                            # Extract call_id from the completed item (use
                            # getattr to handle the union type safely)
                            item = getattr(event, "item", None)
                            item_call_id = getattr(item, "call_id", None) if item else None
                            if item_call_id and pending_function_call:
                                pending_function_call["call_id"] = str(item_call_id)

                            if (
                                pending_function_call
                                and pending_function_call["name"] == "file_metadata_lookup"
                            ):
                                # Execute the direct metadata lookup (bypasses KB)
                                args = json.loads(pending_function_call["arguments"])
                                lookup_result = self.file_metadata_lookup(
                                    args.get("query", user_query)
                                )
                                tool_calls_made.append({
                                    "tool": "file_metadata_lookup",
                                    "arguments": args,
                                    "result": lookup_result,
                                })
                                logger.info(
                                    "Pattern B: file_metadata_lookup executed "
                                    "(query=%r, found=%s, count=%d)",
                                    args.get("query", ""),
                                    lookup_result["found"],
                                    lookup_result.get("document_count", 0),
                                )

                                # Submit function result back to the agent
                                call_id = str(pending_function_call["call_id"])
                                submit_stream = await openai_client.responses.create(
                                    conversation=conversation.id,
                                    input=[{  # type: ignore[arg-type]
                                        "type": "function_call_output",
                                        "call_id": call_id,
                                        "output": json.dumps(lookup_result),
                                    }],
                                    extra_body={
                                        "agent_reference": {
                                            "name": agent.name,
                                            "type": "agent_reference",
                                        }
                                    },
                                    stream=True,
                                )
                                async for sub_event in submit_stream:
                                    if sub_event.type == "response.output_text.delta":
                                        answer_text += sub_event.delta
                                    elif sub_event.type == "response.completed":
                                        if hasattr(sub_event, "response") and hasattr(sub_event.response, "usage"):
                                            usage = sub_event.response.usage
                                            if usage:
                                                token_usage["prompt_tokens"] += getattr(usage, "input_tokens", 0)
                                                token_usage["completion_tokens"] += getattr(usage, "output_tokens", 0)
                                                token_usage["total_tokens"] += getattr(usage, "total_tokens", 0)

                                pending_function_call = None

                            elif pending_function_call:
                                # MCP tool call (knowledge_base_retrieve) —
                                # intercept and execute client-side since the
                                # platform doesn't auto-handle it when mixed
                                # with a FunctionTool.
                                args = json.loads(pending_function_call["arguments"])
                                kb_query = args.get("query", user_query)
                                kb_result = self._execute_kb_retrieve(kb_query)
                                tool_calls_made.append({
                                    "tool": pending_function_call["name"],
                                    "arguments": args,
                                    "result": kb_result,
                                })
                                logger.info(
                                    "Pattern B: knowledge_base_retrieve intercepted "
                                    "(query=%r, found=%s, count=%d)",
                                    kb_query,
                                    kb_result["found"],
                                    kb_result.get("document_count", 0),
                                )

                                # Submit MCP result back to the agent
                                call_id = str(pending_function_call["call_id"])
                                submit_stream = await openai_client.responses.create(
                                    conversation=conversation.id,
                                    input=[{  # type: ignore[arg-type]
                                        "type": "function_call_output",
                                        "call_id": call_id,
                                        "output": json.dumps(kb_result),
                                    }],
                                    extra_body={
                                        "agent_reference": {
                                            "name": agent.name,
                                            "type": "agent_reference",
                                        }
                                    },
                                    stream=True,
                                )
                                async for sub_event in submit_stream:
                                    if sub_event.type == "response.output_text.delta":
                                        answer_text += sub_event.delta
                                    elif sub_event.type == "response.completed":
                                        if hasattr(sub_event, "response") and hasattr(sub_event.response, "usage"):
                                            usage = sub_event.response.usage
                                            if usage:
                                                token_usage["prompt_tokens"] += getattr(usage, "input_tokens", 0)
                                                token_usage["completion_tokens"] += getattr(usage, "output_tokens", 0)
                                                token_usage["total_tokens"] += getattr(usage, "total_tokens", 0)

                                pending_function_call = None

                        # Capture token usage from completed response
                        elif event.type == "response.completed":
                            if hasattr(event, "response") and hasattr(event.response, "usage"):
                                usage = event.response.usage
                                if usage:
                                    token_usage["prompt_tokens"] += getattr(usage, "input_tokens", 0)
                                    token_usage["completion_tokens"] += getattr(usage, "output_tokens", 0)
                                    token_usage["total_tokens"] += getattr(usage, "total_tokens", 0)

                    answer = answer_text or "The agent did not produce a response."

                    # Activity comes from the intercepted KB retrieve call
                    activity = self._last_activity

                    result: Dict[str, Any] = {
                        "status": "completed",
                        "user_query": user_query,
                        "answer": answer,
                        "model": self.deployment_name,
                        "output_mode": self._output_mode.lower(),
                        "pipeline_mode": "pattern_b",
                        "pattern": "B",
                        "is_grounded": True,
                        "tool_calls": tool_calls_made,
                        "token_usage": token_usage,
                        "activity": activity,
                    }

                    # Citation validation
                    if self._validate_citations:
                        validation = self._validate_citation_annotations(answer)
                        result["citation_validation"] = validation

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

    # ------------------------------------------------------------------
    # Citation validation
    # ------------------------------------------------------------------

    def _validate_citation_annotations(self, answer: str) -> Dict[str, Any]:
        """Validate MCP citation annotations in the response."""
        pattern = r"【(\d+):(\d+)†([^】]+)】"
        citations = re.findall(pattern, answer)
        validated = []
        for msg_idx, search_idx, source_name in citations:
            validated.append({
                "message_idx": int(msg_idx),
                "search_idx": int(search_idx),
                "source_name": source_name.strip(),
            })
        return {
            "has_citations": len(validated) > 0,
            "citation_count": len(validated),
            "citations": validated,
        }

    # ==================================================================
    # Public entry-points
    # ==================================================================

    async def process_query_async(self, user_query: str) -> Dict[str, Any]:
        """Process a user query using the Pattern B dual-tool pipeline."""
        logger.info("Processing query (pattern=B): %r", user_query)
        try:
            return await self._execute_pipeline(user_query)
        except Exception as e:
            logger.error("✗ Pattern B pipeline failed: %s", e)
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
