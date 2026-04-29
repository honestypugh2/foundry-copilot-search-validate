"""
Agent 3 – Reference Validator Agent (Azure AI Foundry Agent)

Multi-agent role: Third Foundry Agent in the sequential pipeline.
Extracts structured citation references from validated sources and
uses a Foundry Agent (GPT-4.1) to assess grounding quality.

Core deterministic logic: extract filePath + documentType + metadata.
Foundry Agent enhancement: grounding quality assessment.

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

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "search_config.json"
if _CONFIG_PATH.exists():
    with open(_CONFIG_PATH) as f:
        _AGENT_CONFIG = json.load(f).get("foundry_agent", {})
else:
    _AGENT_CONFIG = {}


class ReferenceValidatorAgent:
    """
    Reference Validator Agent – third Foundry Agent in the sequential pipeline.

    Extracts structured references (filePath, fileName, parentTitle,
    policyNumber, documentType) from validated sources. Uses a Foundry
    Agent to assess grounding quality.  Falls back to deterministic
    mode if the Agents SDK is unavailable.
    """

    AGENT_NAME = "hr-reference-validator-agent"
    INSTRUCTIONS = (
        "You are an HR policy reference validation agent. You receive validated "
        "sources and extracted references from HR policy documents.\n\n"
        "Your task:\n"
        "1. Confirm each reference has a valid file path and policy metadata.\n"
        "2. Assess whether the set of references adequately grounds the response.\n"
        "3. Return ONLY a JSON object:\n"
        "   {\"references\": [...], \"is_grounded\": true/false}\n"
        "4. Each reference must retain: filePath, fileName, parentTitle, "
        "policyNumber, documentType.\n"
        "5. Set is_grounded to true only if at least one reference has a "
        "valid filePath and content from the HR knowledge base."
    )

    def __init__(self):
        self.project_endpoint = os.getenv("AZURE_AI_PROJECT_ENDPOINT", "")
        self.model = _AGENT_CONFIG.get("model", "gpt-4.1")
        self._agent_registered = False

    # ------------------------------------------------------------------
    # Deterministic extraction (always runs)
    # ------------------------------------------------------------------

    def _deterministic_extract(
        self, validated_sources: dict[str, Any]
    ) -> dict[str, Any]:
        """Extract references and assess grounding deterministically."""
        references: list[dict[str, str]] = [
            {
                "filePath": s["filePath"],
                "fileName": s.get("fileName", ""),
                "parentTitle": s.get("parentTitle", ""),
                "policyNumber": s.get("policyNumber", ""),
                "documentType": s["documentType"],
            }
            for s in validated_sources["validated_sources"]
        ]

        is_grounded = len(references) > 0

        if not is_grounded:
            logger.warning("No grounded references found – response may be unsupported")
        else:
            logger.info(
                "Reference validation: %d grounded references", len(references)
            )

        return {
            "references": references,
            "is_grounded": is_grounded,
        }

    # ------------------------------------------------------------------
    # Foundry Agent grounding assessment
    # ------------------------------------------------------------------

    def _agent_assess(self, result: dict[str, Any]) -> dict[str, Any]:
        """Use Foundry Agent to assess grounding quality."""
        if not self._agent_registered:
            ensure_agent(self.AGENT_NAME, self.model, self.INSTRUCTIONS)
            self._agent_registered = True

        context = json.dumps(result, default=str)
        prompt = (
            f"Assess the grounding quality of these references.\n"
            f"Return ONLY a JSON object: "
            f"{{\"references\": [...], \"is_grounded\": true/false}}.\n\n"
            f"{context}"
        )

        text = invoke_agent(self.AGENT_NAME, prompt)

        try:
            parsed = json.loads(text)
            if "references" in parsed and "is_grounded" in parsed:
                return parsed
        except (json.JSONDecodeError, TypeError):
            pass

        return result  # fallback

    # ------------------------------------------------------------------
    # Public entry-point
    # ------------------------------------------------------------------

    def run(self, validated_sources: dict[str, Any]) -> dict[str, Any]:
        """
        Build citation references from validated sources.

        Uses deterministic extraction first, then Foundry Agent for
        grounding quality assessment when available.

        Returns:
            Dict with "references" list and "is_grounded" boolean.
        """
        result = self._deterministic_extract(validated_sources)

        if AGENTS_SDK_AVAILABLE and self.project_endpoint:
            try:
                result = self._agent_assess(result)
            except Exception as e:
                logger.warning(
                    "Foundry Agent grounding assessment failed, using deterministic results: %s", e
                )

        return result
