"""
Agent 4 – Answer Synthesis Agent (Agent Framework + FoundryChatClient)

Agent Framework version using FoundryChatClient for answer synthesis.

Uses FoundryChatClient with GPT-4.1 to synthesize a final
natural-language answer from the validated, grounded search results.

When agentic retrieval has already produced a synthesized answer,
that answer is returned directly without an additional LLM call.

Configuration (from search_config.json → foundry_agent):
    output_mode:           answerSynthesis
    retrieval_reasoning:   medium
    answer_instructions:   ""
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Load foundry agent config
_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "search_config.json"
if _CONFIG_PATH.exists():
    with open(_CONFIG_PATH) as f:
        _AGENT_CONFIG = json.load(f).get("foundry_agent", {})
else:
    _AGENT_CONFIG = {}


class AnswerSynthesisAgent:
    """
    Answer Synthesis Agent – final step in the sequential pipeline.

    In the Agent Framework pattern, the actual LLM invocation is handled
    by the FoundryChatClient Agent via the SequentialBuilder orchestrator.
    This class provides the deterministic logic for context building
    and agentic-answer pass-through.
    """

    INSTRUCTIONS = (
        "You are an HR policy assistant. Answer the user's question based "
        "only on the provided grounded sources. Cite the source file paths "
        "and policy numbers in your answer. If the sources do not contain "
        "enough information, say so.\n\n"
        "Output format:\n"
        "- Provide a clear, concise answer.\n"
        "- Cite sources using [Source N] notation.\n"
        "- If multiple policies apply, reference all relevant ones."
    )

    def __init__(self):
        self.model = _AGENT_CONFIG.get("model", "gpt-4.1")
        self.output_mode = _AGENT_CONFIG.get("output_mode", "answerSynthesis")
        self.retrieval_reasoning = _AGENT_CONFIG.get("retrieval_reasoning", "medium")
        self.answer_instructions = _AGENT_CONFIG.get("answer_instructions", "")

    def build_context_message(
        self, validated_sources: list[dict], references: list[dict]
    ) -> str:
        """Format validated sources into a context block for the agent."""
        parts = []
        for i, src in enumerate(validated_sources, 1):
            content = src.get("content", "")
            file_path = src.get("filePath", "")
            file_name = src.get("fileName", "")
            parent_title = src.get("parentTitle", "")
            policy_number = src.get("policyNumber", "")
            header = f"[Source {i}]"
            if policy_number:
                header += f" Policy {policy_number}"
            if parent_title:
                header += f" – {parent_title}"
            header += f" ({file_name or file_path})"
            parts.append(f"{header}\n{content}")

        context = "\n\n---\n\n".join(parts)

        ref_lines = []
        for ref in references:
            policy = ref.get("policyNumber", "")
            title = ref.get("parentTitle", "")
            path = ref.get("filePath", "")
            label = f"Policy {policy} – {title}" if policy else path
            ref_lines.append(f"- {label} ({ref.get('documentType', '')})")
        ref_block = "\n".join(ref_lines) if ref_lines else "(none)"

        return f"## Grounded Sources\n\n{context}\n\n## References\n\n{ref_block}"

    def run(
        self,
        user_query: str,
        validated_sources: list[dict],
        references: list[dict],
        is_grounded: bool,
        agentic_answer: str | None = None,
    ) -> dict[str, Any]:
        """
        Return synthesis result, preferring agentic retrieval answer.

        When agentic retrieval has already produced a synthesized answer
        (``agentic_answer``), it is returned directly – no extra LLM call
        is needed because the Knowledge Base already performed answer
        synthesis with the configured ``answer_instructions``.

        When no agentic answer is available, the caller (the
        SequentialBuilder orchestrator) will invoke the FoundryChatClient
        Agent to synthesize an answer.

        Returns:
            Dict with answer, model, output_mode, and grounding status.
        """
        if not is_grounded or not validated_sources:
            return {
                "answer": "I could not find grounded information to answer this question.",
                "model": self.model,
                "output_mode": self.output_mode,
                "is_grounded": False,
            }

        # Use agentic retrieval answer when available
        if agentic_answer:
            logger.info("Using agentic retrieval synthesized answer")
            return {
                "answer": agentic_answer,
                "model": self.model,
                "output_mode": self.output_mode,
                "retrieval_reasoning": self.retrieval_reasoning,
                "is_grounded": is_grounded,
                "source": "agentic_retrieval",
            }

        # No agentic answer — return context for FoundryChatClient Agent
        return {
            "answer": None,  # signals that LLM synthesis is needed
            "context": self.build_context_message(validated_sources, references),
            "model": self.model,
            "output_mode": self.output_mode,
            "retrieval_reasoning": self.retrieval_reasoning,
            "is_grounded": is_grounded,
            "source": "pending_foundry_chat_client",
        }
