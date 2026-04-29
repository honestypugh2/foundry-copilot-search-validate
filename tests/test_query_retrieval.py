"""
Query Retrieval Test – Specific Document & Policy Lookups

Tests queries that target specific policy numbers, document titles, or
SOPs by name — analogous to "Find the DFMEA for ST299" or "Show me
results of ON/OFF testing done for FM300" but for the HR knowledge base.

Each test case defines:
    query            – natural-language question
    expected_policy  – policy number that MUST appear in results
    expected_file    – substring of the filename that should appear
    description      – human-readable test label

The script runs each query through the src agent pipeline
(RetrievalAgent → SourceValidatorAgent → ReferenceValidatorAgent)
and reports:
    • actual results (top match details)
    • expected result
    • top reranker score
    • root-cause analysis for any mismatch

Usage:
    # Live mode (requires Azure credentials + indexed data)
    PYTHONPATH=$PWD/src uv run python src/tests/test_query_retrieval.py

    # Mock mode (validates wiring only, no Azure calls)
    PYTHONPATH=$PWD/src uv run python src/tests/test_query_retrieval.py --mock
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
logger = logging.getLogger("test_query_retrieval")

from test_queries import QUERY_EXPECTATIONS

# ---------------------------------------------------------------------------
# Test-case definition
# ---------------------------------------------------------------------------

@dataclass
class QueryTestCase:
    """Single test case for a specific-document retrieval query."""
    query: str
    expected_policy: str           # policy number that MUST appear
    expected_file: str             # substring that MUST appear in fileName
    description: str               # human-readable label
    expected_metadata: dict = field(default_factory=dict)  # expected metadata fields
    # Filled after execution
    actual_matches: list[dict] = field(default_factory=list)
    validated_sources: list[dict] = field(default_factory=list)
    references: list[dict] = field(default_factory=list)
    is_grounded: bool = False
    top_reranker_score: float | None = None
    passed: bool = False
    root_causes: list[str] = field(default_factory=list)
    metadata_checks: dict = field(default_factory=dict)  # per-field pass/fail


# ---------------------------------------------------------------------------
# Test queries – specific document / policy lookups
# ---------------------------------------------------------------------------

TEST_CASES: list[QueryTestCase] = [
    QueryTestCase(
        query=qe.query,
        expected_policy=qe.expected_policy,
        expected_file=qe.expected_file,
        description=qe.description,
        expected_metadata=qe.expected_metadata,
    )
    for qe in QUERY_EXPECTATIONS
]


# ---------------------------------------------------------------------------
# Mock results (for --mock mode)
# ---------------------------------------------------------------------------

def _mock_results_for(tc: QueryTestCase) -> list[dict[str, Any]]:
    """Return synthetic search hits for a test case in mock mode."""
    blob_base = "https://stovcrqidayerac.blob.core.windows.net/ask-hr-knowledge/knowledge_base_lab"
    file_encoded = tc.expected_file.replace(" ", "%20")
    folder = tc.expected_file.rsplit(".", 1)[0] if "." in tc.expected_file else tc.expected_file
    folder_encoded = folder.replace(" ", "%20")
    return [
        {
            "content": f"Mock content for {tc.expected_policy or tc.expected_file}",
            "filePath": f"{blob_base}/{folder_encoded}/{file_encoded}",
            "fileName": tc.expected_file,
            "parentTitle": tc.expected_metadata.get("parent_title", tc.expected_file),
            "policyNumber": tc.expected_metadata.get("parent_title", tc.expected_file),
            "documentType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            "container": "ask-hr-knowledge",
            "score": 12.5,
            "reranker_score": 3.15,
            "blob_url": f"{blob_base}/{folder_encoded}/{file_encoded}",
        }
    ]


# ---------------------------------------------------------------------------
# Evaluate a single test case
# ---------------------------------------------------------------------------

def _evaluate(tc: QueryTestCase) -> None:
    """Analyse matches against expected values and populate root_causes."""
    if not tc.actual_matches:
        tc.passed = False
        tc.root_causes.append("NO_RESULTS: Search returned zero matches")
        return

    top = tc.actual_matches[0]
    tc.top_reranker_score = top.get("reranker_score")

    # Check policy number (policy_number field contains the full filename)
    policy_found = any(
        tc.expected_policy in (m.get("policyNumber", "") or "")
        for m in tc.actual_matches
    ) if tc.expected_policy else True
    file_found = any(
        tc.expected_file in (m.get("fileName", "") or m.get("filePath", ""))
        for m in tc.actual_matches
    )

    # --- Metadata validation ---
    if tc.expected_metadata:
        for key, expected_val in tc.expected_metadata.items():
            # Check if any match has the expected metadata value
            match_key = {
                "parent_title": "parentTitle",
                "container": "container",
                "blob_url": "blob_url",
            }.get(key, key)
            actual_val = None
            found = False
            for m in tc.actual_matches:
                actual = m.get(match_key, "")
                if expected_val and expected_val in (actual or ""):
                    found = True
                    actual_val = actual
                    break
                if not actual_val and actual:
                    actual_val = actual
            tc.metadata_checks[key] = found
            if not found:
                tc.root_causes.append(
                    f"METADATA_MISMATCH: Expected {key}='{expected_val}', "
                    f"got '{actual_val or '(empty)'}'"
                )

    # Check that results have file path references (blob_url or filePath)
    has_path_ref = any(
        m.get("blob_url") or m.get("filePath") for m in tc.actual_matches
    )
    if not has_path_ref:
        tc.root_causes.append(
            "MISSING_PATH_REF: No blob_url or filePath in any match — "
            "file path references are missing from results"
        )

    # --- Root cause analysis ---
    if not policy_found and tc.expected_policy:
        actual_policies = [m.get("policyNumber", "(none)") for m in tc.actual_matches]
        tc.root_causes.append(
            f"WRONG_POLICY: Expected policy '{tc.expected_policy}' not in "
            f"results. Got: {actual_policies}"
        )
        # Deeper diagnosis
        if tc.expected_policy and any(tc.expected_policy in (m.get("content", "")) for m in tc.actual_matches):
            tc.root_causes.append(
                "METADATA_GAP: Policy number appears in content but not in "
                "policyNumber field — index projection or field mapping issue"
            )

    if not file_found:
        actual_files = [m.get("fileName", "(none)") for m in tc.actual_matches]
        tc.root_causes.append(
            f"WRONG_FILE: Expected file containing '{tc.expected_file}' not in "
            f"results. Got: {actual_files}"
        )

    # Reranker-score diagnosis
    if tc.top_reranker_score is not None and tc.top_reranker_score < 1.0:
        tc.root_causes.append(
            f"LOW_RERANKER_SCORE: Top reranker score {tc.top_reranker_score:.3f} < 1.0 — "
            "semantic ranker did not find a strong match"
        )

    # Top-hit is correct but not rank-1
    if not policy_found and tc.expected_policy:
        for i, m in enumerate(tc.actual_matches[1:], 2):
            if tc.expected_policy in (m.get("policyNumber", "") or ""):
                tc.root_causes.append(
                    f"RANK_DEMOTION: Correct policy found at rank {i}, not rank 1 — "
                    "reranker prioritised a different document"
                )
                break

    tc.passed = (policy_found and file_found
                 and all(tc.metadata_checks.values()) if tc.metadata_checks else policy_found and file_found)


# ---------------------------------------------------------------------------
# Run all test cases
# ---------------------------------------------------------------------------

def run_tests(mock: bool) -> list[QueryTestCase]:
    """Execute every test case and return populated results."""

    if not mock:
        from agents.retrieval_agent import RetrievalAgent
        from agents.source_validator_agent import SourceValidatorAgent
        from agents.reference_validator_agent import ReferenceValidatorAgent

        retrieval = RetrievalAgent()
        source_validator = SourceValidatorAgent()
        ref_validator = ReferenceValidatorAgent()
    else:
        retrieval = source_validator = ref_validator = None

    results: list[QueryTestCase] = []

    for tc in TEST_CASES:
        logger.info("─" * 60)
        logger.info("QUERY: %s", tc.query)
        logger.info("  expected_policy=%s  expected_file=%s",
                     tc.expected_policy or "(any)", tc.expected_file)

        if mock:
            tc.actual_matches = _mock_results_for(tc)
            tc.validated_sources = tc.actual_matches
            tc.references = [
                {"filePath": m["filePath"], "fileName": m["fileName"],
                 "policyNumber": m["policyNumber"],
                 "parentTitle": m["parentTitle"],
                 "documentType": m["documentType"]}
                for m in tc.actual_matches
            ]
            tc.is_grounded = True
        else:
            # Step 1: Retrieval Agent
            retrieval_output = retrieval.run(tc.query)
            tc.actual_matches = retrieval_output.get("matches", [])

            # Step 2: Source Validator Agent
            validation_output = source_validator.run(retrieval_output)
            tc.validated_sources = validation_output.get("validated_sources", [])

            # Step 3: Reference Validator Agent
            ref_output = ref_validator.run(validation_output)
            tc.references = ref_output.get("references", [])
            tc.is_grounded = ref_output.get("is_grounded", False)

        _evaluate(tc)
        results.append(tc)

        # --- Print per-query report ---
        logger.info("  RESULT: %s", "PASS ✓" if tc.passed else "FAIL ✗")
        logger.info("  matches=%d  validated=%d  refs=%d  grounded=%s",
                     len(tc.actual_matches), len(tc.validated_sources),
                     len(tc.references), tc.is_grounded)
        logger.info("  top_reranker_score=%s",
                     f"{tc.top_reranker_score:.4f}" if tc.top_reranker_score else "(none)")

        if tc.actual_matches:
            top = tc.actual_matches[0]
            logger.info("  TOP HIT:")
            logger.info("    fileName     : %s", top.get("fileName", "(none)"))
            logger.info("    policyNumber : %s", top.get("policyNumber", "(none)"))
            logger.info("    parentTitle  : %s", top.get("parentTitle", "(none)"))
            logger.info("    blob_url     : %s", top.get("blob_url", "(none)"))
            logger.info("    filePath     : %s", top.get("filePath", "(none)"))
            logger.info("    container    : %s", top.get("container", "(none)"))
            logger.info("    content      : %s...", (top.get("content", ""))[:120])

        if tc.metadata_checks:
            logger.info("  METADATA CHECKS:")
            for key, passed in tc.metadata_checks.items():
                logger.info("    %s: %s", key, "✓" if passed else "✗")

        if tc.root_causes:
            for rc in tc.root_causes:
                logger.warning("  ROOT CAUSE: %s", rc)

    return results


# ---------------------------------------------------------------------------
# Summary report
# ---------------------------------------------------------------------------

def print_summary(results: list[QueryTestCase]) -> None:
    """Print a tabular summary of all test cases."""

    logger.info("")
    logger.info("=" * 80)
    logger.info("QUERY RETRIEVAL TEST SUMMARY")
    logger.info("=" * 80)

    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed

    # Table header
    header = f"{'#':<3} {'Pass':<6} {'Score':<8} {'Expected':<12} {'Actual':<12} {'Query':<50}"
    logger.info(header)
    logger.info("-" * 80)

    for i, tc in enumerate(results, 1):
        status = "✓" if tc.passed else "✗"
        score_str = f"{tc.top_reranker_score:.3f}" if tc.top_reranker_score else "n/a"
        actual_policy = tc.actual_matches[0].get("policyNumber", "(none)") if tc.actual_matches else "(empty)"
        logger.info(
            f"{i:<3} {status:<6} {score_str:<8} {tc.expected_policy or tc.expected_file:<12} "
            f"{actual_policy:<12} {tc.query[:50]}"
        )

    logger.info("-" * 80)
    logger.info("TOTAL: %d/%d passed, %d failed", passed, len(results), failed)

    # Root-cause frequency
    if failed > 0:
        logger.info("")
        logger.info("ROOT CAUSE SUMMARY:")
        cause_counts: dict[str, int] = {}
        for tc in results:
            if not tc.passed:
                for rc in tc.root_causes:
                    tag = rc.split(":")[0]
                    cause_counts[tag] = cause_counts.get(tag, 0) + 1
        for tag, count in sorted(cause_counts.items(), key=lambda x: -x[1]):
            logger.info("  %s: %d occurrence(s)", tag, count)

    logger.info("=" * 80)

    # JSON dump for programmatic consumption
    json_path = Path(__file__).resolve().parent / "query_retrieval_results.json"
    records = []
    for tc in results:
        top_match = tc.actual_matches[0] if tc.actual_matches else {}
        records.append({
            "query": tc.query,
            "description": tc.description,
            "expected_policy": tc.expected_policy,
            "expected_file": tc.expected_file,
            "expected_metadata": tc.expected_metadata,
            "passed": tc.passed,
            "top_reranker_score": tc.top_reranker_score,
            "match_count": len(tc.actual_matches),
            "validated_count": len(tc.validated_sources),
            "reference_count": len(tc.references),
            "is_grounded": tc.is_grounded,
            "actual_top_policy": top_match.get("policyNumber", ""),
            "actual_top_file": top_match.get("fileName", ""),
            "actual_top_blob_url": top_match.get("blob_url", ""),
            "actual_top_file_path": top_match.get("filePath", ""),
            "actual_top_parent_title": top_match.get("parentTitle", ""),
            "actual_top_container": top_match.get("container", ""),
            "metadata_checks": tc.metadata_checks,
            "root_causes": tc.root_causes,
        })
    with open(json_path, "w") as f:
        json.dump(records, f, indent=2)
    logger.info("Detailed results written to %s", json_path)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Query retrieval test — specific document / policy lookups"
    )
    parser.add_argument(
        "--mock", action="store_true",
        help="Run in mock mode (no Azure credentials needed)",
    )
    args = parser.parse_args()

    logger.info("=" * 80)
    logger.info("HR Policy Knowledge Lab – Query Retrieval Test")
    logger.info("Mode: %s  |  Test cases: %d", "MOCK" if args.mock else "LIVE", len(TEST_CASES))
    logger.info("=" * 80)

    # Load .env if present
    try:
        from dotenv import load_dotenv
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path)
            logger.info("Loaded .env from %s", env_path)
    except ImportError:
        pass

    results = run_tests(args.mock)
    print_summary(results)

    failed = sum(1 for r in results if not r.passed)
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
