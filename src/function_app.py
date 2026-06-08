"""
Azure Function App – HTTP endpoints for the HR Policy Knowledge Lab.

Exposes:
  POST /api/ask    — runs the FoundryAgentOrchestrator (Pattern A/B).
                     Default mode (single_agent): a single Foundry Agent with
                     MCPTool retrieves, reasons, and returns citation-backed
                     answers. Optional PIPELINE_MODE=multi_step.
  POST /api/lookup — direct metadata-only lookup (file path / blob URL /
                     filename / policy number) via a single hybrid_search
                     call. NO Foundry, NO MCP, NO LLM synthesis. This is the
                     standalone "Pattern B" file-location tool, intended for
                     Copilot Studio to call directly so simple file/location
                     lookups skip the slower agentic/RAG path.
  GET  /api/health — liveness probe.
"""

import json
import logging
import os

import azure.functions as func
from pydantic import ValidationError

from models import QueryRequest

logger = logging.getLogger(__name__)

app = func.FunctionApp()

# Lazy-init the orchestrator on first request. Doing this at module scope
# would make ANY init failure (missing env var, auth, network) prevent the
# Functions host from registering routes, surfacing as
# "We were not able to load some functions in the list due to errors" in the
# portal with no usable diagnostics.
_orchestrator = None


def _get_orchestrator():
    global _orchestrator
    if _orchestrator is None:
        # Imported lazily so an import-time failure inside the agent stack
        # surfaces as a clear 500 with a logged stack trace rather than
        # killing function registration. The factory honors the
        # ORCHESTRATOR_PATTERN env var (A = single-agent MCP, B = hybrid
        # MCP + metadata lookup).
        from agents.orchestrator_factory import get_orchestrator
        _orchestrator = get_orchestrator()
    return _orchestrator


# Lazy-init a bare Azure AI Search client for the /api/lookup endpoint. This
# is deliberately independent of the orchestrator stack so the metadata lookup
# has no Foundry / MCP / LLM dependency and stays fast.
_search_client = None


def _get_search_client():
    global _search_client
    if _search_client is None:
        from search.azure_ai_search_client import AzureAISearchClient
        _search_client = AzureAISearchClient()
    return _search_client


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
        orchestrator = _get_orchestrator()
        results = await orchestrator.process_query_async(query)
        return _json_response(results, status=200)
    except Exception as e:
        logger.exception("Orchestrator failed: %s", e)
        return _json_response({"error": "Internal server error"}, status=500)


@app.route(route="lookup", methods=["POST"], auth_level=func.AuthLevel.FUNCTION)
def lookup_document(req: func.HttpRequest) -> func.HttpResponse:
    """
    POST /api/lookup

    Body: { "query": "Where is the PTO policy stored?" }

    Direct, deterministic file-location lookup. Runs a single hybrid_search
    against the index and returns metadata fields ONLY (file path, blob URL,
    filename, parent title, policy number). No Foundry agent, no MCP, no LLM
    synthesis — so it returns in ~1–2s instead of the agentic/RAG path.

    Intended to be wired into Copilot Studio as a separate REST API tool so
    the agent can route "where is X" queries here and keep content questions
    on the knowledge source / agent. See copilot/openapi-lookup-v2.json.
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
        top_raw = req.params.get("top")
        top = max(1, min(int(top_raw), 10)) if top_raw else 3
    except (TypeError, ValueError):
        top = 3

    try:
        client = _get_search_client()
        hits = client.hybrid_search(query, top=top)

        documents = [
            {
                "metadata_storage_name": hit.get("fileName", ""),
                "metadata_storage_path": hit.get("filePath", ""),
                "blob_url": hit.get("blob_url", ""),
                "parent_title": hit.get("parentTitle", ""),
                "policy_number": hit.get("policyNumber", ""),
                "reranker_score": hit.get("reranker_score"),
            }
            for hit in hits
        ]

        return _json_response(
            {
                "status": "completed",
                "user_query": query,
                "found": len(documents) > 0,
                "document_count": len(documents),
                "documents": documents,
            },
            status=200,
        )
    except Exception as e:
        logger.exception("Lookup failed: %s", e)
        return _json_response({"error": "Internal server error"}, status=500)


@app.route(route="health", methods=["GET"], auth_level=func.AuthLevel.ANONYMOUS)
def health(req: func.HttpRequest) -> func.HttpResponse:
    """Liveness probe for the Function App."""
    return _json_response({"status": "ok"})
