"""
Azure Function App – HTTP endpoint for the HR Policy Knowledge Lab.

Exposes a POST /api/ask endpoint that runs the FoundryAgentOrchestrator.
Default mode (single_agent): a single Foundry Agent with MCPTool retrieves,
reasons, and returns citation-backed answers to Copilot Studio.
Optional mode (PIPELINE_MODE=multi_step): 4-step sequential pipeline.
"""

import json
import logging
import os

import azure.functions as func
from agents.sequential_orchestrator_foundry import FoundryAgentOrchestrator

logger = logging.getLogger(__name__)

app = func.FunctionApp()

orchestrator = FoundryAgentOrchestrator()


@app.route(route="ask", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
async def ask_hr_policy(req: func.HttpRequest) -> func.HttpResponse:
    """
    POST /api/ask

    Body: { "query": "What is the PTO policy?" }

    Returns grounded HR policy results with references.
    """
    try:
        body = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON body"}),
            status_code=400,
            mimetype="application/json",
        )

    query = body.get("query", "").strip()
    if not query:
        return func.HttpResponse(
            json.dumps({"error": "Missing 'query' field"}),
            status_code=400,
            mimetype="application/json",
        )

    try:
        results = await orchestrator.process_query_async(query)
        return func.HttpResponse(
            json.dumps(results, default=str),
            status_code=200,
            mimetype="application/json",
        )
    except Exception as e:
        logger.error(f"Orchestrator failed: {e}")
        return func.HttpResponse(
            json.dumps({"error": "Internal server error"}),
            status_code=500,
            mimetype="application/json",
        )
