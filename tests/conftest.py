"""
conftest.py – Shared fixtures and configuration for src/tests/

Dual-mode test infrastructure:
    DEFAULT → Live Azure (requires credentials + .env)
    --mock  → Mocked clients (offline, no Azure needed)

Usage:
    # Live (default) – connects to Azure
    PYTHONPATH=$PWD/src pytest src/tests/ -v

    # Mock – no Azure needed
    PYTHONPATH=$PWD/src pytest src/tests/ -v --mock

    # Run only mock-safe tests
    PYTHONPATH=$PWD/src pytest src/tests/ -v -m mock

    # Run only live tests
    PYTHONPATH=$PWD/src pytest src/tests/ -v -m "not mock_only"
"""

import logging
import os
import sys
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Ensure src is importable
# ---------------------------------------------------------------------------
_SRC = str(Path(__file__).resolve().parent.parent)
if _SRC not in sys.path:
    sys.path.insert(0, _SRC)

# ---------------------------------------------------------------------------
# Load .env at collection time
# ---------------------------------------------------------------------------
_ENV_PATH = Path(__file__).resolve().parent.parent / ".env"
try:
    from dotenv import load_dotenv
    if _ENV_PATH.exists():
        load_dotenv(_ENV_PATH, override=True)
except ImportError:
    pass

# ---------------------------------------------------------------------------
# Logging – readable stage output
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="\n%(levelname)-8s │ %(name)s │ %(message)s",
    force=True,
)

# ---------------------------------------------------------------------------
# CLI option: --mock
# ---------------------------------------------------------------------------
def pytest_addoption(parser):
    parser.addoption(
        "--mock",
        action="store_true",
        default=False,
        help="Run tests in mock mode (no Azure credentials needed)",
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def use_mock(request) -> bool:
    """True when tests should use mocked Azure clients."""
    return request.config.getoption("--mock") or os.getenv("USE_MOCK", "").lower() == "true"


@pytest.fixture(scope="session")
def is_live(use_mock) -> bool:
    """True when tests connect to live Azure services."""
    return not use_mock


@pytest.fixture
def test_logger():
    """Pre-configured logger for test output."""
    return logging.getLogger("hr-lab-tests")


# ---------------------------------------------------------------------------
# Shared sample data
# ---------------------------------------------------------------------------

SAMPLE_MATCHES = [
    {
        "content": "PTO is accrued at 1.5 days per month for full-time employees.",
        "filePath": "https://storage.blob.core.windows.net/ask-hr-knowledge/51350.pdf",
        "fileName": "51350 - Types of Leave_ Paid Time Off (PTO) (23472_2).docx",
        "parentTitle": "51350 - Types of Leave_ Paid Time Off (PTO) (23472_2).docx",
        "policyNumber": "51350",
        "documentType": "application/pdf",
        "container": "ask-hr-knowledge",
    },
    {
        "content": "Employees must follow the uniform dress code.",
        "filePath": "https://storage.blob.core.windows.net/ask-hr-knowledge/52005.pdf",
        "fileName": "52005 - Operational Matters_ Uniform Dress Code (2583_19).docx",
        "parentTitle": "52005 - Operational Matters_ Uniform Dress Code (2583_19).docx",
        "policyNumber": "52005",
        "documentType": "application/pdf",
        "container": "ask-hr-knowledge",
    },
]

SAMPLE_UNTRUSTED_MATCH = {
    "content": "Untrusted content",
    "filePath": "/other/doc.pdf",
    "fileName": "doc.pdf",
    "parentTitle": "",
    "policyNumber": "",
    "documentType": "application/pdf",
    "container": "untrusted-container",
}


@pytest.fixture(scope="session")
def sample_matches():
    return SAMPLE_MATCHES


@pytest.fixture(scope="session")
def sample_untrusted():
    return SAMPLE_UNTRUSTED_MATCH
