"""
Agent 2 – Source Validation Agent (Azure AI Foundry Agent)

Multi-agent role: Second Foundry Agent in the sequential pipeline.
Verifies provenance of retrieved documents using a Foundry Agent
(GPT-4.1) to reason about source trustworthiness.

Core deterministic check: container == "ask-hr-knowledge".
Foundry Agent enhancement: trust assessment and reasoning.

Uses azure.ai.projects v2 SDK — agents are registered once and
invoked via the conversations + responses API.

Falls back to deterministic-only mode when the SDK or
AZURE_AI_PROJECT_ENDPOINT is unavailable.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

from agents.foundry_client import (
    PROJECTS_SDK_AVAILABLE as AGENTS_SDK_AVAILABLE,
    ensure_agent,
    invoke_agent,
)

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
    Source Validation Agent – second Foundry Agent in the sequential pipeline.

    Filters retrieval matches to those from the trusted Azure Blob
    Storage container, then uses a Foundry Agent to reason about
    source trustworthiness.  Falls back to deterministic mode if
    the Agents SDK is unavailable.
    """

    AGENT_NAME = "hr-source-validator-agent"
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
        self.project_endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT", "")
        self.model = _AGENT_CONFIG.get("model", "gpt-4.1")
        self._agent_registered = False

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
    # Foundry Agent trust assessment
    # ------------------------------------------------------------------

    def _agent_assess(self, result: dict[str, Any]) -> dict[str, Any]:
        """Use Foundry Agent to assess source trustworthiness."""
        if not self._agent_registered:
            ensure_agent(self.AGENT_NAME, self.model, self.INSTRUCTIONS)
            self._agent_registered = True

        context = json.dumps(result, default=str)
        prompt = (
            f"Validate these pre-filtered HR policy sources.\n"
            f"Return ONLY a JSON object: "
            f"{{\"validated_sources\": [...], \"source_count\": N}}.\n\n"
            f"{context}"
        )

        text = invoke_agent(self.AGENT_NAME, prompt)

        try:
            parsed = json.loads(text)
            if "validated_sources" in parsed:
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass

        return result  # fallback

    # ------------------------------------------------------------------
    # Public entry-point
    # ------------------------------------------------------------------

    def run(self, retrieval_output: dict[str, Any]) -> dict[str, Any]:
        """
        Validate provenance of each match.

        Uses deterministic container filter first, then Foundry Agent
        for trust assessment when available.

        Returns:
            Dict with "validated_sources" and "source_count".
        """
        result = self._deterministic_validate(retrieval_output)

        if AGENTS_SDK_AVAILABLE and self.project_endpoint:
            try:
                result = self._agent_assess(result)
            except Exception as e:
                logger.warning(
                    "Foundry Agent trust assessment failed, using deterministic results: %s", e
                )

        return result
