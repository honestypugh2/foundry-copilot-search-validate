import logging
from typing import List, Dict, Any, Optional
from search.azure_ai_search_client import AzureAISearchClient, AGENTIC_RETRIEVAL_AVAILABLE
from .foundry_client import (
    ensure_agent, 
    invoke_agent, 
    PROJECTS_SDK_AVAILABLE, 
    build_mcp_tool, 
    invoke_agent_with_mcp
)

logger = logging.getLogger(__name__)

class AnswerSynthesisAgent:
    """
    Agent responsible for synthesizing a final answer based on retrieved documents.
    Can operate in standard mode or agentic MCP mode.
    """
    
    AGENT_NAME = "answer-synthesis-agent"
    
    def __init__(self, model: str = "gpt-4", search_client: Optional[AzureAISearchClient] = None):
        self.model = model
        self.search_client = search_client or AzureAISearchClient()
        self._agent_registered = False
        self._use_mcp = False

    def _build_system_instructions(self) -> str:
        return (
            "You are an HR Policy Synthesis Agent. Your goal is to provide clear, "
            "accurate, and concise answers to employee HR questions based ONLY on "
            "the provided policy snippets and context. If the information is not "
            "available in the context, state that you don't know."
        )

    def _build_context_message(self, sources: List[str], references: List[Dict[str, Any]]) -> str:
        context_parts = ["Retrieved Policy Context:"]
        for i, (src, ref) in enumerate(zip(sources, references)):
            context_parts.append(f"Source [{i+1}] ({ref.get('source_file', 'Unknown')}): {src}")
        return "\n\n".join(context_parts)

    def synthesize_answer(self, user_query: str, validated_sources: List[str], references: List[Dict[str, Any]]) -> str:
        """
        Synthesizes an answer using either manual prompt assembly or agentic multi-tool retrieval.
        """
        system_instructions = self._build_system_instructions()
        
        # Check if agentic retrieval should be used via MCP
        mcp_endpoint = self.search_client.get_mcp_endpoint()
        use_agentic = AGENTIC_RETRIEVAL_AVAILABLE and PROJECTS_SDK_AVAILABLE and mcp_endpoint is not None
        
        if not self._agent_registered:
            tools = []
            if use_agentic:
                connection_name = self.search_client.config.get("agentic_retrieval", {}).get("mcp", {}).get("connection_name", "hr-knowledge-mcp")
                tools = [build_mcp_tool(mcp_endpoint, connection_name)]
                self._use_mcp = True
                logger.info("MCP tool configured for answer synthesis agent")
            else:
                self._use_mcp = False

            ensure_agent(self.AGENT_NAME, self.model, system_instructions, tools=tools)
            self._agent_registered = True

        try:
            if self._use_mcp:
                return invoke_agent_with_mcp(self.AGENT_NAME, user_query)
            else:
                context_msg = self._build_context_message(validated_sources, references)
                prompt = f"Question: {user_query}\n\n{context_msg}"
                return invoke_agent(self.AGENT_NAME, prompt)
        except Exception as e:
            logger.error(f"Error in answer synthesis: {e}")
            return "I encountered an error while synthesizing the answer. Please try again or contact HR support."
