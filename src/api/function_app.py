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
from pydantic import ValidationError

from agents.sequential_orchestrator_foundry import FoundryAgentOrchestrator
from models import QueryRequest

logger = logging.getLogger(__name__)

app = func.FunctionApp()

orchestrator = FoundryAgentOrchestrator()


def _json_response(payload: dict, status: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(payload, default=str),
        status_code=status,
        mimetype="application/json",
    )


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
        return _json_response({"error": "Invalid JSON body"}, status=400)

    try:
        request_model = QueryRequest.model_validate(body)
    except ValidationError as e:
        return _json_response(
            {"error": "Validation failed", "details": e.errors()}, status=400
        )

    query = request_model.query.strip()
    if not query:
        return _json_response({"error": "Missing 'query' field"}, status=400)

    try:
        results = await orchestrator.process_query_async(query)
        return _json_response(results, status=200)
    except Exception as e:
        logger.exception("Orchestrator failed: %s", e)
        return _json_response({"error": "Internal server error"}, status=500)


@app.route(route="health", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def health(req: func.HttpRequest) -> func.HttpResponse:
    """Liveness probe for the Function App."""
    return _json_response({"status": "ok"})
