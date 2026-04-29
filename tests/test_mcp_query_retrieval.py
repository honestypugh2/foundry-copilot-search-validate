"""
MCP Query Retrieval Test — FoundryAgentOrchestrator (single-agent MCP)

Sends each canonical test query through the FoundryAgentOrchestrator's
single-agent MCP pipeline and validates that the answer references the
expected policy number and/or filename.

Usage:
    # Live mode (requires Azure credentials + provisioned knowledge base)
    PYTHONPATH=$PWD/src uv run python src/tests/test_mcp_query_retrieval.py

    # Run a subset (first N queries)
    PYTHONPATH=$PWD/src uv run python src/tests/test_mcp_query_retrieval.py --limit 3
"""

import argparse
import json
import logging
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger("test_mcp_query_retrieval")

from test_queries import QUERY_EXPECTATIONS, QueryExpectation


@dataclass
class MCPTestResult:
    query: str
    expected_policy: str
    expected_file: str
    description: str
    answer: str = ""
    passed: bool = False
    policy_found: bool = False
    file_found: bool = False
    has_citations: bool = False
    citation_count: int = 0
    error: str = ""
    root_causes: list[str] = field(default_factory=list)


def evaluate(qe: QueryExpectation, result: dict) -> MCPTestResult:
    tr = MCPTestResult(
        query=qe.query,
        expected_policy=qe.expected_policy,
        expected_file=qe.expected_file,
        description=qe.description,
    )

    if "error" in result and result.get("status") != "completed":
        tr.error = str(result.get("error", "unknown error"))
        tr.root_causes.append(f"PIPELINE_ERROR: {tr.error}")
        return tr

    answer = result.get("answer", "")
    tr.answer = answer

    # Check if expected policy number appears in the answer
    if qe.expected_policy:
        tr.policy_found = qe.expected_policy in answer
        if not tr.policy_found:
            tr.root_causes.append(
                f"POLICY_MISSING: Expected '{qe.expected_policy}' not in answer"
            )
    else:
        tr.policy_found = True  # No policy expected (e.g., SOP docs)

    # Check if expected filename (or key parts) appears in the answer
    # Strip extension for flexible matching
    file_stem = qe.expected_file.rsplit(".", 1)[0] if "." in qe.expected_file else qe.expected_file
    tr.file_found = (
        qe.expected_file in answer
        or file_stem in answer
        # Also check for the policy title portion (after " - ")
        or (
            " - " in qe.expected_file
            and qe.expected_file.split(" - ", 1)[1].split("(")[0].strip() in answer
        )
    )
    if not tr.file_found:
        tr.root_causes.append(
            f"FILE_MISSING: Expected reference to '{qe.expected_file}' not in answer"
        )

    # Check citation annotations
    citation_validation = result.get("citation_validation", {})
    tr.has_citations = citation_validation.get("has_citations", False)
    tr.citation_count = citation_validation.get("citation_count", 0)

    # Also check for citation pattern directly in answer if validation wasn't run
    if not tr.has_citations:
        import re
        citations = re.findall(r"【\d+:\d+†[^】]+】", answer)
        tr.has_citations = len(citations) > 0
        tr.citation_count = len(citations)

    if not tr.has_citations:
        tr.root_causes.append("NO_CITATIONS: No MCP citation annotations in answer")

    tr.passed = tr.policy_found and tr.file_found
    return tr


def run_tests(limit: int | None = None) -> list[MCPTestResult]:
    # Force citation validation on for this test
    os.environ["VALIDATE_CITATIONS"] = "true"

    from agents.sequential_orchestrator_foundry import FoundryAgentOrchestrator

    orchestrator = FoundryAgentOrchestrator()
    orchestrator._validate_citations = True

    expectations = QUERY_EXPECTATIONS[:limit] if limit else QUERY_EXPECTATIONS
    results: list[MCPTestResult] = []

    for i, qe in enumerate(expectations, 1):
        logger.info("─" * 70)
        logger.info("[%d/%d] %s", i, len(expectations), qe.query)
        logger.info("  expected: policy=%s  file=%s",
                     qe.expected_policy or "(any)", qe.expected_file)

        try:
            result = orchestrator.process_query(qe.query)
            tr = evaluate(qe, result)
        except Exception as e:
            logger.error("  EXCEPTION: %s", e)
            tr = MCPTestResult(
                query=qe.query,
                expected_policy=qe.expected_policy,
                expected_file=qe.expected_file,
                description=qe.description,
                error=str(e),
            )
            tr.root_causes.append(f"EXCEPTION: {e}")

        results.append(tr)

        status = "PASS ✓" if tr.passed else "FAIL ✗"
        logger.info("  %s  policy_found=%s  file_found=%s  citations=%d",
                     status, tr.policy_found, tr.file_found, tr.citation_count)
        if tr.answer:
            logger.info("  answer: %s...", tr.answer[:200])
        for rc in tr.root_causes:
            logger.warning("  ROOT CAUSE: %s", rc)

    return results


def print_summary(results: list[MCPTestResult]) -> None:
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed

    logger.info("")
    logger.info("=" * 80)
    logger.info("MCP QUERY RETRIEVAL TEST SUMMARY")
    logger.info("=" * 80)

    header = f"{'#':<3} {'Pass':<6} {'Cites':<6} {'Expected':<12} {'Query':<55}"
    logger.info(header)
    logger.info("-" * 80)

    for i, tr in enumerate(results, 1):
        status = "✓" if tr.passed else "✗"
        logger.info(
            f"{i:<3} {status:<6} {tr.citation_count:<6} "
            f"{tr.expected_policy or tr.expected_file[:12]:<12} "
            f"{tr.query[:55]}"
        )

    logger.info("-" * 80)
    logger.info("TOTAL: %d/%d passed, %d failed", passed, len(results), failed)

    if failed > 0:
        logger.info("")
        logger.info("ROOT CAUSE SUMMARY:")
        cause_counts: dict[str, int] = {}
        for tr in results:
            if not tr.passed:
                for rc in tr.root_causes:
                    tag = rc.split(":")[0]
                    cause_counts[tag] = cause_counts.get(tag, 0) + 1
        for tag, count in sorted(cause_counts.items(), key=lambda x: -x[1]):
            logger.info("  %s: %d occurrence(s)", tag, count)

    logger.info("=" * 80)

    # Write JSON results
    json_path = Path(__file__).resolve().parent / "mcp_query_retrieval_results.json"
    records = []
    for tr in results:
        records.append({
            "query": tr.query,
            "description": tr.description,
            "expected_policy": tr.expected_policy,
            "expected_file": tr.expected_file,
            "passed": tr.passed,
            "policy_found": tr.policy_found,
            "file_found": tr.file_found,
            "has_citations": tr.has_citations,
            "citation_count": tr.citation_count,
            "answer": tr.answer,
            "root_causes": tr.root_causes,
            "error": tr.error,
        })
    with open(json_path, "w") as f:
        json.dump(records, f, indent=2)
    logger.info("Results written to %s", json_path)


def main():
    parser = argparse.ArgumentParser(
        description="MCP query retrieval test — FoundryAgentOrchestrator"
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Run only the first N queries",
    )
    args = parser.parse_args()

    try:
        from dotenv import load_dotenv
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path)
            logger.info("Loaded .env from %s", env_path)
    except ImportError:
        pass

    logger.info("=" * 80)
    logger.info("HR Policy Knowledge Lab – MCP Query Retrieval Test")
    n = args.limit or len(QUERY_EXPECTATIONS)
    logger.info("Pipeline: FoundryAgentOrchestrator (single-agent MCP)")
    logger.info("Test cases: %d", n)
    logger.info("=" * 80)

    results = run_tests(limit=args.limit)
    print_summary(results)

    failed = sum(1 for r in results if not r.passed)
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
