"""
Shared Foundry client helpers for azure.ai.projects v2 SDK.

Provides a singleton AIProjectClient and OpenAI client for agent
registration, conversation management, and responses invocation.
"""

import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

try:
    from azure.ai.projects import AIProjectClient
    from azure.ai.projects.models import MCPTool, PromptAgentDefinition
    from azure.identity import DefaultAzureCredential
    PROJECTS_SDK_AVAILABLE = True
except ImportError:
    PROJECTS_SDK_AVAILABLE = False
    logger.info("azure-ai-projects SDK not installed; agents use deterministic mode only")

_project_client: "AIProjectClient | None" = None
_openai_client: Any = None


def get_project_client() -> "AIProjectClient":
    """Return a shared AIProjectClient (lazy singleton)."""
    global _project_client
    if _project_client is not None:
        return _project_client
    if not PROJECTS_SDK_AVAILABLE:
        raise RuntimeError("azure-ai-projects SDK not installed")
    endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT", "")
    if not endpoint:
        raise RuntimeError("AZURE_AI_PROJECT_ENDPOINT not set")
    _project_client = AIProjectClient(
        endpoint=endpoint,
        credential=DefaultAzureCredential(),
    )
    return _project_client


def get_openai_client():
    """Return a shared OpenAI client obtained from the project client."""
    global _openai_client
    if _openai_client is not None:
        return _openai_client
    _openai_client = get_project_client().get_openai_client()
    return _openai_client


def ensure_agent(agent_name: str, model: str, instructions: str, tools: list | None = None) -> dict[str, Any]:
    """
    Ensure a prompt agent version exists. Creates one if missing.

    Args:
        agent_name: Unique name for the agent.
        model: Model deployment name (e.g. 'gpt-4.1').
        instructions: System instructions for the agent.
        tools: Optional list of tools (e.g. MCPTool instances).

    Returns dict with 'name' and 'version'.
    """
    pc = get_project_client()
    try:
        existing = pc.agents.get(agent_name=agent_name)
        logger.info("Agent '%s' already registered", agent_name)
        return {"name": existing.name, "version": existing.versions.get("latest", {}).get("version", "unknown") if existing.versions else "unknown"}
    except Exception:
        pass

    definition = PromptAgentDefinition(
        model=model,
        instructions=instructions,
        temperature=0.0,
    )
    if tools:
        definition = PromptAgentDefinition(
            model=model,
            instructions=instructions,
            temperature=0.0,
            tools=tools,
        )

    agent = pc.agents.create_version(
        agent_name=agent_name,
        definition=definition,
    )
    logger.info("Agent '%s' registered (version=%s)", agent.name, agent.version)
    return {"name": agent.name, "version": agent.version}


def invoke_agent(agent_name: str, user_message: str) -> str:
    """
    Invoke a registered agent via conversations + responses API.

    Creates an ephemeral conversation, sends the user message,
    gets the agent's response, deletes the conversation, and
    returns the response text.
    """
    oai = get_openai_client()

    conversation = oai.conversations.create(
        items=[{"type": "message", "role": "user", "content": user_message}],
    )

    response = oai.responses.create(
        conversation=conversation.id,
        extra_body={
            "agent_reference": {
                "name": agent_name,
                "type": "agent_reference",
            }
        },
    )

    text = response.output_text

    try:
        oai.conversations.delete(conversation_id=conversation.id)
    except Exception:
        pass

    return text


def invoke_agent_with_mcp(agent_name: str, user_message: str) -> str:
    """
    Invoke a registered agent with ``tool_choice="required"`` so it
    always calls the MCP tool for knowledge base grounding.

    Creates an ephemeral conversation, sends the user message with
    tool_choice=required, gets the response, cleans up, and returns
    the response text.
    """
    oai = get_openai_client()

    conversation = oai.conversations.create()

    response = oai.responses.create(
        conversation=conversation.id,
        tool_choice="required",
        input=user_message,
        extra_body={
            "agent": {
                "name": agent_name,
                "type": "agent_reference",
            }
        },
    )

    text = response.output_text

    try:
        oai.conversations.delete(conversation_id=conversation.id)
    except Exception:
        pass

    return text


def build_mcp_tool(mcp_endpoint: str, connection_name: str) -> "MCPTool":
    """Build an MCPTool pointing to the knowledge base MCP endpoint."""
    return MCPTool(
        server_label="knowledge-base",
        server_url=mcp_endpoint,
        require_approval="never",
        allowed_tools=["knowledge_base_retrieve"],
        project_connection_id=connection_name,
    )
