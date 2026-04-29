"""
Agent 1 – Retrieval Agent (Agentic Retrieval / Azure AI Search)

Agent Framework version using FoundryChatClient.

Multi-agent role: First agent in the sequential pipeline.
Uses Azure AI Search **agentic retrieval** (Knowledge Base) to:
  1. Decompose complex queries into focused subqueries.
  2. Run subqueries against the hr_lab_index knowledge source.
  3. Rerank with semantic ranker.
  4. Synthesize a natural-language answer with citations.

Falls back to deterministic hybrid search (text + vector + semantic
ranker) when agentic retrieval is unavailable.
"""

import json
import logging
from pathlib import Path
from typing import Any

from search.azure_ai_search_client import AzureAISearchClient, AGENTIC_RETRIEVAL_AVAILABLE

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "search_config.json"
if _CONFIG_PATH.exists():
    with open(_CONFIG_PATH) as f:
        _CFG = json.load(f)
        _AGENT_CONFIG = _CFG.get("foundry_agent", {})
        _AGENTIC_CONFIG = _CFG.get("agentic_retrieval", {})
else:
    _AGENT_CONFIG = {}
    _AGENTIC_CONFIG = {}


class RetrievalAgent:
    """
    Retrieval Agent – first agent in the sequential pipeline.

    Primary path: **Agentic Retrieval** via ``KnowledgeBaseRetrievalClient``.
    The knowledge base decomposes the query, runs subqueries against
    the HR knowledge source, reranks, and synthesises an answer.

    Fallback: deterministic hybrid search (text + vector + semantic
    ranker) when agentic retrieval is not configured or fails.

    Note: This agent wraps the same AzureAISearchClient as agents/
    but is designed to be used within the Agent Framework Executor pattern.
    """

    def __init__(self):
        self.search_client = AzureAISearchClient()
        self.model = _AGENT_CONFIG.get("model", "gpt-4.1")
        self.retrieval_instructions = _AGENT_CONFIG.get("retrieval_instructions", "")
        self._agentic_enabled = (
            AGENTIC_RETRIEVAL_AVAILABLE
            and bool(_AGENTIC_CONFIG.get("knowledge_base_name"))
        )

    # ------------------------------------------------------------------
    # Agentic retrieval (primary path)
    # ------------------------------------------------------------------

    def _agentic_search(self, user_query: str) -> dict[str, Any]:
        """Execute agentic retrieval via the Knowledge Base."""
        messages = [{"role": "user", "content": user_query}]
        result = self.search_client.agentic_retrieve(messages)

        return {
            "matches": result["matches"],
            "agentic_answer": result.get("response", ""),
            "activity": result.get("activity", []),
        }

    # ------------------------------------------------------------------
    # Deterministic hybrid search (fallback)
    # ------------------------------------------------------------------

    def _deterministic_search(
        self, user_query: str, embedding: list[float] | None = None
    ) -> list[dict[str, Any]]:
        """Execute hybrid search and return raw structured hits."""
        hits = self.search_client.hybrid_search(user_query, embedding)
        return [
            {
                "content": h["content"],
                "filePath": h["filePath"],
                "fileName": h["fileName"],
                "parentTitle": h["parentTitle"],
                "policyNumber": h["policyNumber"],
                "documentType": h["documentType"],
                "container": h["container"],
            }
            for h in hits
        ]

    # ------------------------------------------------------------------
    # Public entry-point
    # ------------------------------------------------------------------

    def run(
        self, user_query: str, embedding: list[float] | None = None
    ) -> dict[str, Any]:
        """
        Search the HR knowledge index and return matches.

        Uses **agentic retrieval** (Knowledge Base) when available,
        which provides query planning, subquery decomposition,
        semantic ranking, and answer synthesis.  Falls back to
        deterministic hybrid search otherwise.

        Returns:
            Dict with ``matches`` list, and optionally
            ``agentic_answer`` and ``activity`` when agentic
            retrieval is used.
        """
        if self._agentic_enabled:
            try:
                result = self._agentic_search(user_query)
                logger.info(
                    "Agentic retrieval returned %d matches",
                    len(result["matches"]),
                )
                return result
            except Exception as e:
                logger.warning(
                    "Agentic retrieval failed, falling back to hybrid search: %s", e
                )

        # Fallback: deterministic hybrid search
        matches = self._deterministic_search(user_query, embedding)
        return {"matches": matches}
