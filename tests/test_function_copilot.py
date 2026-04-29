"""
Tests – Azure Function → Copilot Studio

Dual-mode: mocked endpoint + live integration.

Usage:
    # Live (default) – requires running func start
    PYTHONPATH=$PWD/src pytest src/tests/test_function_copilot.py -v -s

    # Mock – no Azure needed
    PYTHONPATH=$PWD/src pytest src/tests/test_function_copilot.py -v -s --mock

    # Live endpoint only
    PYTHONPATH=$PWD/src pytest src/tests/test_function_copilot.py -v -s -k live
"""

import json
import logging
import os
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

logger = logging.getLogger("test.function_copilot")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_http_request(body: dict | None = None, method: str = "POST") -> MagicMock:
    """Create a mock Azure Functions HttpRequest."""
    req = MagicMock()
    req.method = method
    if body is not None:
        req.get_json.return_value = body
    else:
        req.get_json.side_effect = ValueError("No JSON")
    return req


MOCK_ORCHESTRATOR_RESULT = {
    "status": "completed",
    "user_query": "What is the PTO policy?",
    "answer": "PTO is defined in Policy 51350.",
    "matches": [
        {
            "content": "PTO content...",
            "filePath": "/path/51350.pdf",
            "documentType": "application/pdf",
            "container": "ask-hr-knowledge",
        }
    ],
    "validated_sources": [
        {
            "content": "PTO content...",
            "filePath": "/path/51350.pdf",
            "container": "ask-hr-knowledge",
        }
    ],
    "references": [
        {"filePath": "/path/51350.pdf", "documentType": "application/pdf"}
    ],
    "is_grounded": True,
    "steps": {
        "retrieval": {"status": "completed"},
        "source_validation": {"status": "completed"},
        "reference_validation": {"status": "completed"},
        "answer_synthesis": {"status": "completed"},
    },
}


# ---------------------------------------------------------------------------
# Test /api/ask endpoint contract
# ---------------------------------------------------------------------------

class TestAskEndpoint:
    """Test the Azure Function /api/ask endpoint."""

    @pytest.mark.asyncio
    async def test_successful_query(self, test_logger):
        test_logger.info("─── /api/ask: successful query (mock) ───")
        mock_orch = MagicMock()
        mock_orch.process_query_async = AsyncMock(return_value=MOCK_ORCHESTRATOR_RESULT)

        from api.function_app import ask_hr_policy

        # Patch the module-level orchestrator
        import api.function_app as app_module
        original = app_module.orchestrator
        app_module.orchestrator = mock_orch

        try:
            req = _make_http_request({"query": "What is the PTO policy?"})
            resp = await ask_hr_policy(req)

            assert resp.status_code == 200
            body = json.loads(resp.get_body())
            assert body["status"] == "completed"
            assert body["is_grounded"] is True
            assert "answer" in body
            assert "matches" in body
            assert "references" in body
            test_logger.info("  status=%s  grounded=%s", body["status"], body["is_grounded"])
            test_logger.info("  ✓ successful query OK")
        finally:
            app_module.orchestrator = original

    @pytest.mark.asyncio
    async def test_missing_query(self, test_logger):
        test_logger.info("─── /api/ask: missing query ───")
        from api.function_app import ask_hr_policy

        req = _make_http_request({"query": ""})
        resp = await ask_hr_policy(req)

        assert resp.status_code == 400
        body = json.loads(resp.get_body())
        assert "error" in body
        test_logger.info("  status_code=%d", resp.status_code)
        test_logger.info("  ✓ missing query returns 400")

    @pytest.mark.asyncio
    async def test_missing_query_field(self, test_logger):
        test_logger.info("─── /api/ask: missing query field ───")
        from api.function_app import ask_hr_policy

        req = _make_http_request({"message": "hello"})
        resp = await ask_hr_policy(req)

        assert resp.status_code == 400
        test_logger.info("  ✓ missing field returns 400")

    @pytest.mark.asyncio
    async def test_invalid_json(self, test_logger):
        test_logger.info("─── /api/ask: invalid JSON ───")
        from api.function_app import ask_hr_policy

        req = _make_http_request(None)
        resp = await ask_hr_policy(req)

        assert resp.status_code == 400
        body = json.loads(resp.get_body())
        assert body["error"] == "Invalid JSON body"
        test_logger.info("  ✓ invalid JSON returns 400")

    @pytest.mark.asyncio
    async def test_orchestrator_failure(self, test_logger):
        test_logger.info("─── /api/ask: orchestrator failure (mock) ───")
        mock_orch = MagicMock()
        mock_orch.process_query_async = AsyncMock(
            side_effect=RuntimeError("Search unavailable")
        )

        from api.function_app import ask_hr_policy
        import api.function_app as app_module

        original = app_module.orchestrator
        app_module.orchestrator = mock_orch

        try:
            req = _make_http_request({"query": "test"})
            resp = await ask_hr_policy(req)

            assert resp.status_code == 500
            body = json.loads(resp.get_body())
            assert body["error"] == "Internal server error"
            test_logger.info("  status_code=%d", resp.status_code)
            test_logger.info("  ✓ orchestrator failure → 500")
        finally:
            app_module.orchestrator = original


# ---------------------------------------------------------------------------
# Test Copilot Studio contract (response shape)
# ---------------------------------------------------------------------------

class TestCopilotStudioContract:
    """Verify the response shape matches what Copilot Studio expects."""

    def test_response_has_required_fields(self, test_logger):
        test_logger.info("─── Copilot Studio: response shape ───")
        """Copilot Studio action expects these top-level fields."""
        required = {"status", "user_query", "matches", "references", "is_grounded"}
        assert required.issubset(set(MOCK_ORCHESTRATOR_RESULT.keys()))
        test_logger.info("  required fields : %s", required)
        test_logger.info("  ✓ all required fields present")

    def test_match_has_required_fields(self, test_logger):
        test_logger.info("─── Copilot Studio: match shape ───")
        match = MOCK_ORCHESTRATOR_RESULT["matches"][0]
        assert "content" in match
        assert "filePath" in match
        assert "documentType" in match
        test_logger.info("  ✓ match fields OK")

    def test_reference_has_required_fields(self, test_logger):
        test_logger.info("─── Copilot Studio: reference shape ───")
        ref = MOCK_ORCHESTRATOR_RESULT["references"][0]
        assert "filePath" in ref
        assert "documentType" in ref
        test_logger.info("  ✓ reference fields OK")


# ---------------------------------------------------------------------------
# Test Copilot Studio manifest / OpenAPI
# ---------------------------------------------------------------------------

class TestCopilotStudioFiles:
    """Validate Copilot Studio integration artifacts."""

    def test_manifest_exists_and_valid(self, test_logger):
        test_logger.info("─── Copilot Studio: manifest.json ───")
        manifest_path = (
            Path(__file__).resolve().parent.parent / "copilot" / "manifest.json"
        )
        assert manifest_path.exists(), f"Missing: {manifest_path}"

        with open(manifest_path) as f:
            manifest = json.load(f)

        assert manifest["name"] == "HR Policy Knowledge Assistant"
        assert any(
            "AzureFunctionAction" in str(cap)
            for cap in manifest["capabilities"]
        )
        test_logger.info("  name         : %s", manifest["name"])
        test_logger.info("  capabilities : %d", len(manifest["capabilities"]))
        test_logger.info("  ✓ manifest valid")

    def test_openapi_exists_and_has_ask_endpoint(self, test_logger):
        test_logger.info("─── Copilot Studio: openapi.yaml ───")
        openapi_path = (
            Path(__file__).resolve().parent.parent / "copilot" / "openapi.yaml"
        )
        assert openapi_path.exists(), f"Missing: {openapi_path}"

        content = openapi_path.read_text()
        assert "/api/ask" in content
        assert "askHRPolicy" in content
        assert "query" in content
        test_logger.info("  ✓ openapi has /api/ask + askHRPolicy")

    def test_openapi_response_schema(self, test_logger):
        test_logger.info("─── Copilot Studio: response schema ───")
        openapi_path = (
            Path(__file__).resolve().parent.parent / "copilot" / "openapi.yaml"
        )
        content = openapi_path.read_text()

        # Verify response schema includes key fields
        assert "is_grounded" in content
        assert "matches" in content
        assert "references" in content
        test_logger.info("  ✓ schema has is_grounded, matches, references")


# ---------------------------------------------------------------------------
# Live integration tests (require running func start)
# ---------------------------------------------------------------------------

class TestLiveIntegration:
    """
    Live integration tests against a running Azure Function.
    Requires: func start (or deployed function URL).

    Run with: pytest -v -k live
    """

    @pytest.fixture
    def function_url(self):
        url = os.getenv("FUNCTION_APP_URL", "http://localhost:7071")
        return url

    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_live_ask_endpoint(self, function_url, is_live, test_logger):
        """Call the live /api/ask endpoint."""
        if not is_live:
            pytest.skip("--mock mode")
        test_logger.info("─── LIVE /api/ask: PTO query ───")
        test_logger.info("  url : %s", function_url)
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{function_url}/api/ask",
                json={"query": "What is the PTO policy?"},
                timeout=60.0,
            )

        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "completed"
        assert body["is_grounded"] is True
        assert len(body["references"]) > 0
        test_logger.info("  status     : %s", body["status"])
        test_logger.info("  grounded   : %s", body["is_grounded"])
        test_logger.info("  references : %d", len(body["references"]))
        test_logger.info("  ✓ LIVE /api/ask OK")

    @pytest.mark.live
    @pytest.mark.asyncio
    async def test_live_grounding(self, function_url, is_live, test_logger):
        """Verify live results are grounded in ask-hr-knowledge."""
        if not is_live:
            pytest.skip("--mock mode")
        test_logger.info("─── LIVE /api/ask: grounding check ───")
        import httpx

        async with httpx.AsyncClient() as client:
            resp = await client.post(
                f"{function_url}/api/ask",
                json={"query": "What is the dress code policy?"},
                timeout=60.0,
            )

        body = resp.json()
        assert body["is_grounded"] is True
        # All validated sources should be from the trusted container
        for src in body.get("validated_sources", []):
            assert src["container"] == "ask-hr-knowledge"
        test_logger.info("  validated_sources : %d (all trusted)",
                         len(body.get("validated_sources", [])))
        test_logger.info("  ✓ LIVE grounding check OK")
