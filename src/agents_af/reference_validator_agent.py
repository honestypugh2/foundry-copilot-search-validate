"""
Agent 3 – Reference Validator Agent (Agent Framework + FoundryChatClient)

Agent Framework version using FoundryChatClient for grounding assessment.

Multi-agent role: Third agent in the sequential pipeline.
Extracts structured citation references from validated sources and
optionally uses a FoundryChatClient Agent (GPT-4.1) to assess
grounding quality.

Core deterministic logic: extract filePath + documentType + metadata.
FoundryChatClient Agent enhancement: grounding quality assessment.
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "search_config.json"
if _CONFIG_PATH.exists():
    with open(_CONFIG_PATH) as f:
        _AGENT_CONFIG = json.load(f).get("foundry_agent", {})
else:
    _AGENT_CONFIG = {}


class ReferenceValidatorAgent:
    """
    Reference Validator Agent – third agent in the sequential pipeline.

    Extracts structured references (filePath, fileName, parentTitle,
    policyNumber, documentType) from validated sources.  Grounding
    quality assessment via FoundryChatClient is performed at the
    Executor/Agent level in the sequential orchestrator.

    In the Agent Framework pattern, this agent is wrapped by a custom
    Executor (ReferenceValidationExecutor) in the SequentialBuilder.
    """

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
        self.model = _AGENT_CONFIG.get("model", "gpt-4.1")

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
    # Public entry-point
    # ------------------------------------------------------------------

    def run(self, validated_sources: dict[str, Any]) -> dict[str, Any]:
        """
        Build citation references from validated sources.

        Uses deterministic extraction.  FoundryChatClient-based grounding
        assessment is handled at the Executor/Agent level in the
        sequential orchestrator.

        Returns:
            Dict with "references" list and "is_grounded" boolean.
        """
        return self._deterministic_extract(validated_sources)
