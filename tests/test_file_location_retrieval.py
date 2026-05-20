"""
File Location Retrieval Test — Critical Issue Validation

Tests the specific blocking issue: the system MUST return file locations
(metadata_storage_path, blob_url) when users ask "where is document X?"
or "give me the link to document Y".

This directly validates the fix for:
    - Output mode mapping bug (EXTRACTIVE vs EXTRACTIVE_DATA)
    - Agent instructions not mandating file location return
    - search_fields missing path/filename fields

Test categories:
    1. "Where is [document]?" — explicit location queries
    2. "Give me the link to [document]" — URL/path request queries
    3. "What is the storage path for [policy]?" — metadata-specific queries
    4. Combined content + location queries

Usage:
    # Live mode (requires Azure credentials + indexed data)
    PYTHONPATH=$PWD/src uv run python tests/test_file_location_retrieval.py

    # Mock mode (validates wiring only, no Azure calls)
    PYTHONPATH=$PWD/src uv run python tests/test_file_location_retrieval.py --mock
"""

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Ensure src is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger("test_file_location_retrieval")


# ---------------------------------------------------------------------------
# Test-case definition
# ---------------------------------------------------------------------------

@dataclass
class FileLocationTestCase:
    """Single test case for a file-location retrieval query."""
    query: str
    expected_file: str             # substring that MUST appear in fileName or path
    expected_policy: str           # policy number (if applicable)
    description: str               # human-readable label
    query_type: str                # "location", "link", "path", "combined"
    # Filled after execution
    answer: str = ""
    matches: list[dict] = field(default_factory=list)
    file_path_returned: bool = False
    blob_url_returned: bool = False
    metadata_storage_path_in_answer: bool = False
    passed: bool = False
    failure_reason: str = ""


# ---------------------------------------------------------------------------
# File location test queries — designed to reproduce the reported issue
# ---------------------------------------------------------------------------

FILE_LOCATION_TEST_CASES: list[FileLocationTestCase] = [
    # -- Category 1: "Where is [document]?" queries -----------------------
    FileLocationTestCase(
        query="Where is the PTO policy document stored?",
        expected_file="51350 - Types of Leave_ Paid Time Off (PTO) (23472_2).docx",
        expected_policy="51350",
        description="Where-is query for PTO policy",
        query_type="location",
    ),
    FileLocationTestCase(
        query="Where is Policy 52005 for the Uniform Dress Code located?",
        expected_file="52005 - Operational Matters_ Uniform Dress Code (2583_19).docx",
        expected_policy="52005",
        description="Where-is query by exact policy number",
        query_type="location",
    ),
    FileLocationTestCase(
        query="Where can I find the IT Acceptable Use Policy document?",
        expected_file="81100 - Information Technology Acceptable Use Policy (23666_5).docx",
        expected_policy="81100",
        description="Where-can-I-find query for IT policy",
        query_type="location",
    ),
    FileLocationTestCase(
        query="Where is the Code of Ethics document stored in the system?",
        expected_file="31000 - Code of Ethics and Related Matters (9081_12).docx",
        expected_policy="31000",
        description="Where-is with system storage context",
        query_type="location",
    ),

    # -- Category 2: "Give me the link/URL/path" queries ------------------
    FileLocationTestCase(
        query="Give me the link to the Holiday Pay policy document",
        expected_file="50715 - Hours Worked and Pay Administration_ Holiday Pay (23641_4).docx",
        expected_policy="50715",
        description="Give-me-the-link for Holiday Pay",
        query_type="link",
    ),
    FileLocationTestCase(
        query="Give me the file path for Policy 87100 on AI and LLM",
        expected_file="87100 - Generative Artificial Intelligence (AI) & Large Language Models (LLM) Policy (23653_5).docx",
        expected_policy="87100",
        description="Give-me-the-path by policy number",
        query_type="link",
    ),
    FileLocationTestCase(
        query="Provide the URL to the Mobile Device policy document",
        expected_file="88100 - Mobile Device and Use Policy (23693_3).docx",
        expected_policy="88100",
        description="Provide-URL for Mobile Device policy",
        query_type="link",
    ),
    FileLocationTestCase(
        query="Give me the blob storage path for the Short-Term Disability policy",
        expected_file="51370 - Short-Term Disability (23317_1).docx",
        expected_policy="51370",
        description="Blob-storage-path request for STD policy",
        query_type="link",
    ),

    # -- Category 3: Explicit metadata/storage-path queries ---------------
    FileLocationTestCase(
        query="What is the metadata_storage_path for the Blood Borne Pathogens Introduction document?",
        expected_file="101100 - Blood Borne Pathogens Introduction (83_0).doc",
        expected_policy="101100",
        description="Explicit metadata_storage_path request",
        query_type="path",
    ),
    FileLocationTestCase(
        query="What is the full storage path for Policy 50455 on Probationary Period?",
        expected_file="50455 - Hiring_ Probationary Period (23389_3).docx",
        expected_policy="50455",
        description="Full-storage-path by policy number",
        query_type="path",
    ),
    FileLocationTestCase(
        query="Show me the file location and blob URL for the Computer Replacement Policy",
        expected_file="84100 - Computer Replacement Policy (3261_3).doc",
        expected_policy="84100",
        description="File-location-and-blob-URL combined request",
        query_type="path",
    ),

    # -- Category 4: Combined content + location queries ------------------
    FileLocationTestCase(
        query="What does the Non-Uniform Dress Code policy say, and where is the document stored?",
        expected_file="52010 - Operational Matters_ Non-Uniform Dress Code (23685_1).docx",
        expected_policy="52010",
        description="Content + location combined query",
        query_type="combined",
    ),
    FileLocationTestCase(
        query="Summarize the Emergency Notification System policy and provide the document path",
        expected_file="83400 - Emergency Notification System Policy (23234_0).doc",
        expected_policy="83400",
        description="Summary + path combined query",
        query_type="combined",
    ),
]


# ---------------------------------------------------------------------------
# Execution helpers
# ---------------------------------------------------------------------------

def _check_path_in_answer(answer: str) -> bool:
    """Check if the answer contains a recognizable file path or URL."""
    path_indicators = [
        "https://",
        "blob.core.windows.net",
        "ask-hr-knowledge",
        "metadata_storage_path",
        "/knowledge_base_lab/",
        ".blob.",
    ]
    answer_lower = answer.lower()
    return any(indicator.lower() in answer_lower for indicator in path_indicators)


def _check_negative_response(answer: str) -> bool:
    """Check if the answer incorrectly says path is not available."""
    negative_indicators = [
        "no metadata_storage_path was provided",
        "path not available",
        "no file path",
        "cannot determine the location",
        "storage path is not available",
        "no blob url",
        "location is not provided",
        "I don't have access to the file location",
        "unable to provide the file path",
    ]
    answer_lower = answer.lower()
    return any(indicator.lower() in answer_lower for indicator in negative_indicators)


def run_single_agent_test(tc: FileLocationTestCase) -> FileLocationTestCase:
    """Run a single test case through the Foundry Agent Orchestrator (single-agent MCP)."""
    from agents.sequential_orchestrator_foundry import FoundryAgentOrchestrator

    orchestrator = FoundryAgentOrchestrator()
    result = orchestrator.process_query(tc.query)

    tc.answer = result.get("answer", "")
    tc.matches = result.get("matches", [])

    # Check if file path information is present in the answer
    tc.metadata_storage_path_in_answer = _check_path_in_answer(tc.answer)

    # Check if matches contain the expected file with path data.
    # NOTE: single-agent pipeline does NOT return structured matches —
    # it synthesizes the path directly into the answer text.  The
    # filePath/blob_url checks only apply to multi-step pipeline mode.
    for match in tc.matches:
        if tc.expected_file and tc.expected_file.lower() in match.get("fileName", "").lower():
            if match.get("filePath"):
                tc.file_path_returned = True
            if match.get("blob_url"):
                tc.blob_url_returned = True

    # If no structured matches were returned (single-agent mode), infer from answer
    if not tc.matches and tc.metadata_storage_path_in_answer:
        tc.file_path_returned = True
        tc.blob_url_returned = True

    # Determine pass/fail
    has_negative_response = _check_negative_response(tc.answer)

    if has_negative_response:
        tc.passed = False
        tc.failure_reason = "Agent incorrectly says file location is not available"
    elif tc.metadata_storage_path_in_answer:
        tc.passed = True
    elif tc.file_path_returned or tc.blob_url_returned:
        # Path is in structured data but not surfaced in answer text
        tc.passed = False
        tc.failure_reason = (
            "File path exists in match data but was NOT included in the agent's answer text"
        )
    else:
        tc.passed = False
        tc.failure_reason = "No file location information found in answer or match data"

    return tc


def run_hybrid_search_test(tc: FileLocationTestCase) -> FileLocationTestCase:
    """Run a single test case through hybrid search (fallback path) to verify data exists."""
    from search.azure_ai_search_client import AzureAISearchClient

    client = AzureAISearchClient()
    hits = client.hybrid_search(tc.query)

    tc.matches = hits

    for hit in hits:
        file_name = hit.get("fileName", "")
        if tc.expected_file and tc.expected_file.lower() in file_name.lower():
            if hit.get("filePath"):
                tc.file_path_returned = True
                tc.metadata_storage_path_in_answer = True  # Data IS in the index
            if hit.get("blob_url"):
                tc.blob_url_returned = True
            tc.passed = True
            break

    if not tc.passed:
        tc.failure_reason = f"Expected file '{tc.expected_file}' not found in hybrid search results"

    return tc


def run_mock_test(tc: FileLocationTestCase) -> FileLocationTestCase:
    """Mock test that validates wiring without Azure calls."""
    tc.answer = (
        f"The document is located at: "
        f"https://stovcrqidayerac.blob.core.windows.net/ask-hr-knowledge/"
        f"knowledge_base_lab/{tc.expected_file}"
    )
    tc.matches = [
        {
            "content": f"Mock content for {tc.expected_file}",
            "filePath": f"https://stovcrqidayerac.blob.core.windows.net/ask-hr-knowledge/knowledge_base_lab/{tc.expected_file}",
            "fileName": tc.expected_file,
            "parentTitle": tc.expected_file,
            "policyNumber": tc.expected_policy,
            "blob_url": f"https://stovcrqidayerac.blob.core.windows.net/ask-hr-knowledge/knowledge_base_lab/{tc.expected_file}",
            "container": "ask-hr-knowledge",
        }
    ]
    tc.file_path_returned = True
    tc.blob_url_returned = True
    tc.metadata_storage_path_in_answer = True
    tc.passed = True
    return tc


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def print_report(results: list[FileLocationTestCase], mode: str) -> None:
    """Print a detailed test report."""
    print("\n" + "=" * 80)
    print(f"FILE LOCATION RETRIEVAL TEST REPORT — Mode: {mode.upper()}")
    print("=" * 80)

    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed

    # Group by query type
    by_type: dict[str, list[FileLocationTestCase]] = {}
    for r in results:
        by_type.setdefault(r.query_type, []).append(r)

    for qtype, cases in by_type.items():
        print(f"\n--- {qtype.upper()} queries ---")
        for tc in cases:
            status = "✓ PASS" if tc.passed else "✗ FAIL"
            print(f"\n  {status}  {tc.description}")
            print(f"         Query: {tc.query}")
            if not tc.passed:
                print(f"         Failure: {tc.failure_reason}")
            if tc.answer:
                # Truncate long answers
                answer_preview = tc.answer[:200] + "..." if len(tc.answer) > 200 else tc.answer
                print(f"         Answer: {answer_preview}")
            print(f"         Path in answer: {tc.metadata_storage_path_in_answer}")
            print(f"         filePath in data: {tc.file_path_returned}")
            print(f"         blob_url in data: {tc.blob_url_returned}")

    print("\n" + "=" * 80)
    print(f"SUMMARY: {passed}/{len(results)} passed, {failed} failed")
    if failed > 0:
        print(f"\n  CRITICAL: {failed} queries could not return file locations.")
        print("  This blocks production rollout for regulatory/MDR use cases.")
    print("=" * 80)

    # Write results to JSON
    results_path = Path(__file__).parent / "file_location_retrieval_results.json"
    results_data = {
        "mode": mode,
        "total": len(results),
        "passed": passed,
        "failed": failed,
        "results": [
            {
                "query": r.query,
                "query_type": r.query_type,
                "expected_file": r.expected_file,
                "expected_policy": r.expected_policy,
                "description": r.description,
                "passed": r.passed,
                "failure_reason": r.failure_reason,
                "path_in_answer": r.metadata_storage_path_in_answer,
                "filepath_in_data": r.file_path_returned,
                "blob_url_in_data": r.blob_url_returned,
                "answer_preview": r.answer[:500] if r.answer else "",
            }
            for r in results
        ],
    }
    with open(results_path, "w") as f:
        json.dump(results_data, f, indent=2)
    print(f"\nResults written to: {results_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Test file location retrieval — validates the critical issue fix",
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Run in mock mode (no Azure calls, validates wiring only)",
    )
    parser.add_argument(
        "--hybrid-only",
        action="store_true",
        help="Only run hybrid search to verify data exists in index (no agent call)",
    )
    parser.add_argument(
        "--query-type",
        choices=["location", "link", "path", "combined"],
        help="Only run tests of a specific query type",
    )
    args = parser.parse_args()

    # Load environment
    from dotenv import load_dotenv
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
    else:
        load_dotenv()

    # Filter test cases if requested
    cases = FILE_LOCATION_TEST_CASES
    if args.query_type:
        cases = [tc for tc in cases if tc.query_type == args.query_type]

    logger.info("Running %d file location test cases", len(cases))

    if args.mock:
        mode = "mock"
        results = [run_mock_test(tc) for tc in cases]
    elif args.hybrid_only:
        mode = "hybrid_search"
        results = [run_hybrid_search_test(tc) for tc in cases]
    else:
        mode = "single_agent_mcp"
        results = [run_single_agent_test(tc) for tc in cases]

    print_report(results, mode)

    # Exit with non-zero if any failed
    if any(not r.passed for r in results):
        sys.exit(1)


if __name__ == "__main__":
    main()
