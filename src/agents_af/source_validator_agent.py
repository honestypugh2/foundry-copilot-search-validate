"""
Agent 2 – Source Validation Agent (Agent Framework + FoundryChatClient)

Agent Framework version using FoundryChatClient for trust assessment.

Multi-agent role: Second agent in the sequential pipeline.
Verifies provenance of retrieved documents using a FoundryChatClient
Agent (GPT-4.1) to reason about source trustworthiness.

Core deterministic check: container == "ask-hr-knowledge".
FoundryChatClient Agent enhancement: trust assessment and reasoning.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

TRUSTED_CONTAINER = "ask-hr-knowledge"

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "search_config.json"
if _CONFIG_PATH.exists():
    with open(_CONFIG_PATH) as f:
        _AGENT_CONFIG = json.load(f).get("foundry_agent", {})
else:
    _AGENT_CONFIG = {}


class SourceValidatorAgent:
    """
    Source Validation Agent – second agent in the sequential pipeline.

    Filters retrieval matches to those from the trusted Azure Blob
    Storage container, then optionally uses a FoundryChatClient Agent
    to reason about source trustworthiness.

    In the Agent Framework pattern, this agent is wrapped by a custom
    Executor (SourceValidationExecutor) in the SequentialBuilder.
    """

    INSTRUCTIONS = (
        "You are an HR policy source validation agent. You receive search results "
        "that have been pre-filtered to trusted sources from the 'ask-hr-knowledge' "
        "Azure Blob Storage container.\n\n"
        "Your task:\n"
        "1. Confirm each source is a legitimate HR policy document.\n"
        "2. Flag any sources that appear to be duplicates or low-quality.\n"
        "3. Return ONLY a JSON object: {\"validated_sources\": [...], \"source_count\": N}.\n"
        "4. Preserve all field values exactly as provided. Do not fabricate data.\n"
        "5. Each validated source must retain: content, filePath, fileName, "
        "parentTitle, policyNumber, documentType, container."
    )

    def __init__(self):
        self.model = _AGENT_CONFIG.get("model", "gpt-4.1")

    # ------------------------------------------------------------------
    # Deterministic filter (always runs)
    # ------------------------------------------------------------------

    def _deterministic_validate(
        self, retrieval_output: dict[str, Any]
    ) -> dict[str, Any]:
        """Filter matches to trusted container sources."""
        validated: list[dict[str, Any]] = []

        for item in retrieval_output["matches"]:
            if item["container"] == TRUSTED_CONTAINER:
                validated.append(item)
            else:
                logger.warning(
                    "Source rejected – container '%s' is not trusted (filePath=%s)",
                    item.get("container"),
                    item.get("filePath"),
                )

        logger.info(
            "Source validation: %d/%d matches from trusted container",
            len(validated),
            len(retrieval_output["matches"]),
        )

        return {
            "validated_sources": validated,
            "source_count": len(validated),
        }

    # ------------------------------------------------------------------
    # Public entry-point
    # ------------------------------------------------------------------

    def run(self, retrieval_output: dict[str, Any]) -> dict[str, Any]:
        """
        Validate provenance of each match.

        Uses deterministic container filter.  FoundryChatClient-based
        trust assessment is handled at the Executor/Agent level in the
        sequential orchestrator when the agent is invoked via
        FoundryChatClient.

        Returns:
            Dict with "validated_sources" and "source_count".
        """
        return self._deterministic_validate(retrieval_output)
