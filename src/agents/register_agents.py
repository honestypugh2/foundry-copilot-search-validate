#!/usr/bin/env python3
"""
Register all HR policy Foundry Agents via azure.ai.projects v2 SDK.

Run once (or idempotently) to create prompt agents that persist in the
Foundry Portal.  Agents are then invoked at runtime via the conversations +
responses API (see foundry_client.invoke_agent).

Usage:
    python -m agents.register_agents          # from src/
    python src/agents/register_agents.py  # from repo root

Requires:
    AZURE_AI_PROJECT_ENDPOINT  (Foundry project endpoint)
    FOUNDRY_MODEL_NAME or model from search_config.json → foundry_agent.model
"""

import json
import logging
import os
import sys
from pathlib import Path

# Ensure src is on the path when run directly
_SRC = Path(__file__).resolve().parent.parent
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s  %(message)s")
logger = logging.getLogger(__name__)

from agents.foundry_client import ensure_agent  # noqa: E402

# Load model name from config
_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "search_config.json"
if _CONFIG_PATH.exists():
    with open(_CONFIG_PATH) as f:
        _AGENT_CONFIG = json.load(f).get("foundry_agent", {})
else:
    _AGENT_CONFIG = {}

MODEL = os.getenv("FOUNDRY_MODEL_NAME") or _AGENT_CONFIG.get("model", "gpt-4.1")

# ── Agent definitions ────────────────────────────────────────────────

AGENTS = [
    {
        "name": "hr-source-validator-agent",
        "instructions": (
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
        ),
    },
    {
        "name": "hr-reference-validator-agent",
        "instructions": (
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
        ),
    },
    {
        "name": "hr-policy-answer-agent",
        "instructions": (
            _AGENT_CONFIG.get("answer_instructions", "")
            or (
                "You are an HR policy assistant. Answer the user's question "
                "based only on the provided grounded sources. Cite the source "
                "file paths and policy numbers in your answer. If the sources "
                "do not contain enough information, say so."
            )
        ),
    },
]


def main():
    logger.info("Registering %d Foundry Agents (model=%s)", len(AGENTS), MODEL)
    for agent_def in AGENTS:
        result = ensure_agent(
            agent_name=agent_def["name"],
            model=MODEL,
            instructions=agent_def["instructions"],
        )
        logger.info("  ✓ %s  (version=%s)", result["name"], result["version"])
    logger.info("All agents registered successfully")


if __name__ == "__main__":
    main()
