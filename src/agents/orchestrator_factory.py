"""
Orchestrator Factory — Pattern A / Pattern B routing.

Selects the appropriate orchestrator based on ``ORCHESTRATOR_PATTERN`` env var:
    A (default) — Single-agent MCP (instruction-driven file-location)
    B           — Hybrid: MCP + Direct Metadata Lookup (deterministic file paths)

Usage:
    from agents.orchestrator_factory import get_orchestrator
    orchestrator = get_orchestrator()
    result = orchestrator.process_query("Where is the PTO policy?")
"""

import os
import logging

logger = logging.getLogger(__name__)

ORCHESTRATOR_PATTERN = os.getenv("ORCHESTRATOR_PATTERN", "A").upper()


def get_orchestrator():
    """Return the appropriate orchestrator based on ORCHESTRATOR_PATTERN env var."""
    if ORCHESTRATOR_PATTERN == "B":
        from agents.orchestrator_pattern_b import PatternBOrchestrator
        logger.info("Orchestrator factory: Pattern B (Hybrid MCP + Metadata Lookup)")
        return PatternBOrchestrator()
    else:
        from agents.sequential_orchestrator_foundry import FoundryAgentOrchestrator
        logger.info("Orchestrator factory: Pattern A (Single-agent MCP)")
        return FoundryAgentOrchestrator()
