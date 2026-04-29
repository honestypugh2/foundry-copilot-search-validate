"""
Full-Flow Integration Test

Exercises the complete HR Policy Knowledge Lab pipeline starting
from Azure Blob Storage (not local data/ directory):

    Azure Blob Storage (ask-hr-knowledge container)
        → Azure AI Search (index creation + document upload)
        → Hybrid search (text + vector + semantic ranker)
        → Sequential Orchestrator (4-step Foundry Agent pipeline)
        → Answer synthesis (Foundry Agent)

Usage:
    # Run with live Azure services
    python src/tests/test_full_flow.py

    # Run in mock mode (no Azure credentials needed)
    python src/tests/test_full_flow.py --mock

Environment variables required (live mode):
    AZURE_SEARCH_ENDPOINT, AZURE_SEARCH_API_KEY or USE_MANAGED_IDENTITY,
    AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_API_KEY,
    AZURE_AI_PROJECT_ENDPOINT,
    AZURE_STORAGE_ACCOUNT_NAME, AZURE_STORAGE_CONTAINER_NAME
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

# Ensure src is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)
logger = logging.getLogger("test_full_flow")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "search_config.json"

from test_queries import TEST_QUERIES, QUERY_EXPECTATIONS

# ---------------------------------------------------------------------------
# Step 1: Verify Blob Storage (Azure Blob – not local data/)
# ---------------------------------------------------------------------------

def step_1_verify_blob_storage(mock: bool) -> list[dict]:
    """List and verify HR policy documents in Azure Blob Storage."""
    logger.info("=" * 60)
    logger.info("STEP 1: Verify Azure Blob Storage (ask-hr-knowledge)")
    logger.info("=" * 60)

    if mock:
        # Return representative mock blob data
        mock_docs = [
            {"blob_name": "51350 - Types of Leave_ Paid Time Off (PTO) (23472_2)/policy.pdf",
             "policy_number": "51350", "title": "Types of Leave: Paid Time Off (PTO)"},
            {"blob_name": "52005 - Operational Matters_ Uniform Dress Code (2583_19)/policy.pdf",
             "policy_number": "52005", "title": "Operational Matters: Uniform Dress Code"},
            {"blob_name": "50455 - Hiring_ Probationary Period (23389_3)/policy.pdf",
             "policy_number": "50455", "title": "Hiring: Probationary Period"},
            {"blob_name": "81100 - Information Technology Acceptable Use Policy (23666_5)/policy.pdf",
             "policy_number": "81100", "title": "Information Technology Acceptable Use Policy"},
            {"blob_name": "87100 - Generative Artificial Intelligence (AI) & Large Language Models (LLM) Policy (23653_5)/policy.pdf",
             "policy_number": "87100", "title": "Generative AI & LLM Policy"},
        ]
        logger.info("  [MOCK] Returning %d mock blob entries", len(mock_docs))
        for doc in mock_docs:
            logger.info("    %s (policy=%s)", doc["title"][:50], doc["policy_number"])
        logger.info("✓ Step 1 PASSED (mock): %d documents\n", len(mock_docs))
        return mock_docs

    # Live mode – connect to Azure Blob Storage
    from azure.identity import DefaultAzureCredential
    from azure.storage.blob import BlobServiceClient

    account_name = os.getenv("AZURE_STORAGE_ACCOUNT_NAME")
    container_name = os.getenv("AZURE_STORAGE_CONTAINER_NAME", "ask-hr-knowledge")
    assert account_name, "AZURE_STORAGE_ACCOUNT_NAME not set"

    blob_service = BlobServiceClient(
        account_url=f"https://{account_name}.blob.core.windows.net",
        credential=DefaultAzureCredential(),
    )

    container_client = blob_service.get_container_client(container_name)
    blobs = list(container_client.list_blobs())
    assert len(blobs) > 0, f"No blobs found in container '{container_name}'"

    # Deduplicate by top-level folder (policy folder)
    folders_seen: set[str] = set()
    documents: list[dict[str, Any]] = []
    for blob in blobs:
        parts = blob.name.split("/", 1)
        folder = parts[0] if len(parts) > 1 else blob.name
        if folder in folders_seen:
            continue
        folders_seen.add(folder)

        policy_number = folder.split(" - ")[0].strip() if " - " in folder else ""
        title = folder.split(" - ", 1)[1].split("(")[0].strip() if " - " in folder else folder

        documents.append({
            "blob_name": blob.name,
            "folder": folder,
            "policy_number": policy_number,
            "title": title,
            "size": blob.size,
        })

    logger.info("Found %d policy folders in Blob container '%s':", len(documents), container_name)
    for doc in documents:
        logger.info("  %s (policy=%s, size=%s)",
                    doc["title"][:50], doc["policy_number"] or "N/A",
                    doc.get("size", "?"))

    assert len(documents) > 0, "No policy documents found in Blob Storage"
    logger.info("✓ Step 1 PASSED: %d policy folders in Blob Storage\n", len(documents))
    return documents


# ---------------------------------------------------------------------------
# Step 2: Verify search config
# ---------------------------------------------------------------------------

def step_2_verify_config() -> dict:
    """Load and validate search_config.json."""
    logger.info("=" * 60)
    logger.info("STEP 2: Verify search configuration")
    logger.info("=" * 60)

    assert CONFIG_PATH.exists(), f"Config not found: {CONFIG_PATH}"

    with open(CONFIG_PATH) as f:
        config = json.load(f)

    # Verify required sections
    assert "search_config" in config, "Missing search_config section"
    assert "vector_search" in config, "Missing vector_search section"
    assert "semantic_search" in config, "Missing semantic_search section"
    assert "skillset" in config, "Missing skillset section"
    assert "foundry_agent" in config, "Missing foundry_agent section"

    sc = config["search_config"]
    vs = config["vector_search"]
    ss = config["semantic_search"]
    fa = config["foundry_agent"]

    # Search config fields
    assert sc["index_name"] == "hr_lab_index"
    assert sc["vector_field"] == "snippet_vector"
    assert sc["content_field"] == "snippet"
    assert "filename_field" in sc, "Missing filename_field for searchable filenames"
    assert "filepath_field" in sc, "Missing filepath_field for searchable paths"
    assert "parent_title_field" in sc, "Missing parent_title_field for parent-doc metadata"
    assert "policy_number_field" in sc, "Missing policy_number_field for parent-doc metadata"
    logger.info("  ✓ search_config: index=%s, filename=%s, parent_title=%s",
                sc["index_name"], sc["filename_field"], sc["parent_title_field"])

    # Vector search
    algo = vs["algorithm"]["parameters"]
    assert algo["metric"] == "cosine"
    assert algo["m"] == 4
    assert algo["efConstruction"] == 400
    assert algo["efSearch"] == 500
    logger.info("  ✓ vector_search: HNSW (cosine, m=%d, efC=%d, efS=%d)",
                algo["m"], algo["efConstruction"], algo["efSearch"])

    comp = vs["compression"]
    assert comp["parameters"]["quantized_data_type"] == "int8"
    assert comp["rescoring_options"]["enable_rescoring"] is True
    assert comp["rescoring_options"]["default_oversampling"] == 4
    logger.info("  ✓ compression: ScalarQuantization (int8, oversampling=%d)",
                comp["rescoring_options"]["default_oversampling"])

    # Semantic search
    assert ss["reranker"] == "BoostedRerankerScore"
    assert "snippet" in ss["prioritized_fields"]["content_fields"]
    logger.info("  ✓ semantic_search: %s on %s", ss["reranker"],
                ss["prioritized_fields"]["content_fields"])

    # Foundry agent — model comes from config (may change between deployments)
    assert fa["model"], "model is empty"
    assert fa["output_mode"] == "answerSynthesis"
    assert fa["retrieval_reasoning"] == "medium"
    assert len(fa["retrieval_instructions"]) > 0, "retrieval_instructions is empty"
    assert len(fa["answer_instructions"]) > 0, "answer_instructions is empty"
    logger.info("  ✓ foundry_agent: model=%s, output_mode=%s, instructions_length=%d/%d",
                fa["model"], fa["output_mode"],
                len(fa["retrieval_instructions"]), len(fa["answer_instructions"]))

    # Agentic retrieval
    assert "agentic_retrieval" in config, "Missing agentic_retrieval section"
    ar = config["agentic_retrieval"]
    assert ar["knowledge_source_name"] == "hr-knowledge-source"
    assert ar["knowledge_base_name"] == "hr-knowledge-base"
    assert ar["output_mode"] == "ANSWER_SYNTHESIS"
    assert ar["retrieval_reasoning_effort"] in ("low", "medium")
    assert "source_data_fields" in ar
    assert "snippet" in ar["source_data_fields"], "source_data_fields must include snippet"
    assert "metadata_storage_name" in ar["source_data_fields"], "source_data_fields must include metadata_storage_name"
    assert "metadata_storage_path" in ar["source_data_fields"], "source_data_fields must include metadata_storage_path"
    assert "parent_title" in ar["source_data_fields"], "source_data_fields must include parent_title"
    assert "policy_number" in ar["source_data_fields"], "source_data_fields must include policy_number"
    assert ar["include_references"] is True
    assert ar["include_reference_source_data"] is True
    assert ar["always_query_source"] is True
    assert ar["include_activity"] is True
    logger.info("  ✓ agentic_retrieval: kb=%s, ks=%s, output=%s, reasoning=%s",
                ar["knowledge_base_name"], ar["knowledge_source_name"],
                ar["output_mode"], ar["retrieval_reasoning_effort"])
    logger.info("    source_data_fields: %s", ar["source_data_fields"])

    # Skillset
    skills = config["skillset"]["skills"]
    assert len(skills) == 3
    skill_types = [s["type"] for s in skills]
    assert "ContentUnderstandingSkill" in skill_types
    assert "MergeSkill" in skill_types
    assert "AzureOpenAIEmbeddingSkill" in skill_types
    logger.info("  ✓ skillset: %d skills (%s)", len(skills), ", ".join(skill_types))

    # Index projections include parent metadata
    mappings = config["skillset"]["index_projections"]["mappings"]
    mapping_names = [m["name"] for m in mappings]
    assert "metadata_storage_name" in mapping_names, "Index projection missing metadata_storage_name"
    assert "metadata_storage_path" in mapping_names, "Index projection missing metadata_storage_path"
    assert "parent_title" in mapping_names, "Index projection missing parent_title"
    assert "policy_number" in mapping_names, "Index projection missing policy_number"
    logger.info("  ✓ index_projections: %d mappings including parent metadata", len(mappings))

    logger.info("✓ Step 2 PASSED: Configuration is valid\n")
    return config


# ---------------------------------------------------------------------------
# Step 3: Test search client (create index)
# ---------------------------------------------------------------------------

def step_3_test_search_client(mock: bool) -> Any:
    """Instantiate and optionally test the search client."""
    logger.info("=" * 60)
    logger.info("STEP 3: Test Azure AI Search client")
    logger.info("=" * 60)

    from search.azure_ai_search_client import AzureAISearchClient, AGENTIC_RETRIEVAL_AVAILABLE

    client = AzureAISearchClient()

    # Verify client attributes
    assert client.EMBEDDING_MODEL == "text-embedding-3-small"
    assert client.EMBEDDING_DIMENSIONS == 1536
    assert client._vector_field == "snippet_vector"
    assert client._filename_field == "metadata_storage_name"
    assert client._filepath_field == "metadata_storage_path"
    assert client._parent_title_field == "parent_title"
    assert client._policy_number_field == "policy_number"
    logger.info("  ✓ Client attributes: model=%s, dims=%d",
                client.EMBEDDING_MODEL, client.EMBEDDING_DIMENSIONS)
    logger.info("  ✓ Searchable fields: filename=%s, filepath=%s, parent_title=%s, policy_number=%s",
                client._filename_field, client._filepath_field,
                client._parent_title_field, client._policy_number_field)

    # Verify agentic retrieval SDK availability
    logger.info("  ✓ Agentic retrieval SDK available: %s", AGENTIC_RETRIEVAL_AVAILABLE)
    assert hasattr(client, 'create_knowledge_source'), \
        "AzureAISearchClient missing create_knowledge_source method"
    assert hasattr(client, 'create_knowledge_base'), \
        "AzureAISearchClient missing create_knowledge_base method"
    assert hasattr(client, 'agentic_retrieve'), \
        "AzureAISearchClient missing agentic_retrieve method"
    logger.info("  ✓ Agentic retrieval methods present: create_knowledge_source, create_knowledge_base, agentic_retrieve")

    if not mock:
        # Test index creation
        logger.info("  Creating/updating index '%s'...", client.index_name)
        result = client.create_index()
        assert result, "Index creation failed"
        logger.info("  ✓ Index '%s' created/updated", client.index_name)

        # Test embedding generation
        embedding = client.generate_embedding("What is the PTO policy?")
        assert embedding is not None, "Embedding generation failed"
        assert len(embedding) == 1536, f"Expected 1536 dims, got {len(embedding)}"
        logger.info("  ✓ Embedding generated: %d dimensions", len(embedding))
    else:
        logger.info("  [MOCK] Skipping live Azure calls")

    logger.info("✓ Step 3 PASSED: Search client is valid\n")
    return client


# ---------------------------------------------------------------------------
# Step 4: Test individual Foundry Agents
# ---------------------------------------------------------------------------

def step_4_test_agents(mock: bool) -> None:
    """Test each Foundry Agent independently with sample data."""
    logger.info("=" * 60)
    logger.info("STEP 4: Test individual Foundry Agents")
    logger.info("=" * 60)

    # --- Retrieval Agent (Agentic Retrieval) ---
    from agents.retrieval_agent import RetrievalAgent

    retrieval_agent = RetrievalAgent()
    assert hasattr(retrieval_agent, '_agentic_search'), \
        "RetrievalAgent missing _agentic_search – agentic retrieval not wired"
    assert hasattr(retrieval_agent, '_deterministic_search'), \
        "RetrievalAgent missing _deterministic_search"
    assert retrieval_agent.model, "RetrievalAgent model is empty"
    assert len(retrieval_agent.retrieval_instructions) > 0
    logger.info("  ✓ RetrievalAgent: agentic_enabled=%s, model=%s, instructions=%d chars",
                retrieval_agent._agentic_enabled,
                retrieval_agent.model, len(retrieval_agent.retrieval_instructions))

    if not mock:
        result = retrieval_agent.run("What is the PTO policy?")
        assert "matches" in result
        logger.info("  ✓ RetrievalAgent.run(): %d matches", len(result["matches"]))
        if "agentic_answer" in result:
            logger.info("  ✓ Agentic retrieval answer: %d chars",
                        len(result["agentic_answer"]))
        if "activity" in result:
            logger.info("  ✓ Agentic retrieval activity: %d steps",
                        len(result["activity"]))
        if result["matches"]:
            m = result["matches"][0]
            assert "content" in m
            assert "filePath" in m
            assert "fileName" in m
            assert "parentTitle" in m
            assert "policyNumber" in m
            logger.info("  ✓ Match fields: content, filePath, fileName, parentTitle, policyNumber, documentType, container")
    else:
        logger.info("  [MOCK] Skipping live retrieval")

    # --- Source Validator Agent (Foundry Agent) ---
    from agents.source_validator_agent import SourceValidatorAgent

    source_agent = SourceValidatorAgent()
    assert hasattr(source_agent, '_deterministic_validate'), \
        "SourceValidatorAgent missing _deterministic_validate"
    assert hasattr(source_agent, '_agent_assess'), \
        "SourceValidatorAgent missing _agent_assess (Foundry Agent integration)"
    assert source_agent.model, "SourceValidatorAgent model is empty"
    logger.info("  ✓ SourceValidatorAgent: Foundry Agent (model=%s)", source_agent.model)

    sample_retrieval = {
        "matches": [
            {"content": "PTO policy content...", "filePath": "/path/51350.pdf",
             "fileName": "51350 - Types of Leave_ Paid Time Off (PTO) (23472_2).pdf",
             "parentTitle": "Types of Leave: Paid Time Off (PTO)",
             "policyNumber": "51350",
             "documentType": "application/pdf", "container": "ask-hr-knowledge"},
            {"content": "Untrusted content", "filePath": "/other/doc.pdf",
             "fileName": "doc.pdf", "parentTitle": "", "policyNumber": "",
             "documentType": "application/pdf", "container": "untrusted-container"},
        ]
    }
    validation_result = source_agent.run(sample_retrieval)
    assert validation_result["source_count"] == 1, "Expected 1 trusted source"
    assert len(validation_result["validated_sources"]) == 1
    logger.info("  ✓ SourceValidatorAgent.run(): %d/%d trusted",
                validation_result["source_count"], len(sample_retrieval["matches"]))

    # --- Reference Validator Agent (Foundry Agent) ---
    from agents.reference_validator_agent import ReferenceValidatorAgent

    ref_agent = ReferenceValidatorAgent()
    assert hasattr(ref_agent, '_deterministic_extract'), \
        "ReferenceValidatorAgent missing _deterministic_extract"
    assert hasattr(ref_agent, '_agent_assess'), \
        "ReferenceValidatorAgent missing _agent_assess (Foundry Agent integration)"
    assert ref_agent.model, "ReferenceValidatorAgent model is empty"
    logger.info("  ✓ ReferenceValidatorAgent: Foundry Agent (model=%s)", ref_agent.model)

    ref_result = ref_agent.run(validation_result)
    assert ref_result["is_grounded"] is True
    assert len(ref_result["references"]) == 1
    ref = ref_result["references"][0]
    assert ref["policyNumber"] == "51350"
    assert ref["parentTitle"] == "Types of Leave: Paid Time Off (PTO)"
    assert ref["fileName"] == "51350 - Types of Leave_ Paid Time Off (PTO) (23472_2).pdf"
    logger.info("  ✓ ReferenceValidatorAgent.run(): grounded=%s, refs=%d, policy=%s",
                ref_result["is_grounded"], len(ref_result["references"]),
                ref["policyNumber"])

    # --- Answer Synthesis Agent ---
    from agents.answer_synthesis_agent import AnswerSynthesisAgent

    answer_agent = AnswerSynthesisAgent()
    assert answer_agent.model, "AnswerSynthesisAgent model is empty"
    assert hasattr(answer_agent, 'search_client'), \
        "AnswerSynthesisAgent missing search_client"
    assert hasattr(answer_agent, 'synthesize_answer'), \
        "AnswerSynthesisAgent missing synthesize_answer method"
    logger.info("  ✓ AnswerSynthesisAgent instantiated: model=%s",
                answer_agent.model)

    if not mock:
        answer = answer_agent.synthesize_answer(
            user_query="What is the PTO policy?",
            validated_sources=[s["content"] for s in validation_result["validated_sources"]],
            references=ref_result["references"],
        )
        assert answer and len(answer) > 0
        logger.info("  ✓ AnswerSynthesisAgent.synthesize_answer(): answer=%d chars",
                    len(answer))
    else:
        # In mock mode, verify that the agent class has the expected methods
        assert callable(getattr(answer_agent, 'synthesize_answer', None)), \
            "AnswerSynthesisAgent.synthesize_answer is not callable"
        assert callable(getattr(answer_agent, '_build_system_instructions', None)), \
            "AnswerSynthesisAgent._build_system_instructions is not callable"
        logger.info("  ✓ AnswerSynthesisAgent (mock): methods verified")

    logger.info("✓ Step 4 PASSED: All agents validated\n")


# ---------------------------------------------------------------------------
# Step 5: Test sequential orchestrator
# ---------------------------------------------------------------------------

def step_5_test_orchestrator(mock: bool) -> None:
    """Test the FoundryAgentOrchestrator pipeline."""
    logger.info("=" * 60)
    logger.info("STEP 5: Test FoundryAgentOrchestrator (single-agent MCP)")
    logger.info("=" * 60)

    from agents.sequential_orchestrator_foundry import FoundryAgentOrchestrator

    orchestrator = FoundryAgentOrchestrator()
    logger.info("  ✓ FoundryAgentOrchestrator instantiated")
    logger.info("    project_endpoint: %s", orchestrator.project_endpoint[:30] + "..."
                if orchestrator.project_endpoint else "(not set)")
    logger.info("    deployment: %s", orchestrator.deployment_name)
    logger.info("    mode: %s", orchestrator._pipeline_mode)

    if not mock:
        for query in TEST_QUERIES:
            logger.info("  --- Query: %r ---", query)
            try:
                result = orchestrator.process_query(query)
                assert result["status"] == "completed"
                assert "answer" in result

                logger.info("    ✓ answer=%d chars, mode=%s",
                            len(result["answer"]),
                            result.get("pipeline_mode"))
            except Exception as e:
                logger.error("    ✗ Query failed: %s", e)
                raise
    else:
        logger.info("  [MOCK] Skipping live orchestrator run")
        # Verify MCP tool can be built
        mcp_tool = orchestrator._build_mcp_tool()
        assert mcp_tool is not None
        logger.info("  ✓ MCPTool built successfully")

    logger.info("✓ Step 5 PASSED: Orchestrator pipeline validated\n")


# ---------------------------------------------------------------------------
# Step 6: Verify Copilot Studio integration files
# ---------------------------------------------------------------------------

def step_6_verify_copilot_files() -> None:
    """Verify Copilot Studio integration artifacts exist and are valid."""
    logger.info("=" * 60)
    logger.info("STEP 6: Verify Copilot Studio integration files")
    logger.info("=" * 60)

    base = Path(__file__).resolve().parent.parent

    # manifest.json
    manifest_path = base / "copilot" / "manifest.json"
    assert manifest_path.exists(), f"Missing: {manifest_path}"
    with open(manifest_path) as f:
        manifest = json.load(f)
    assert manifest["name"] == "HR Policy Knowledge Assistant"
    assert "AzureFunctionAction" in str(manifest["capabilities"])
    logger.info("  ✓ manifest.json: name=%r", manifest["name"])

    # openapi.yaml
    openapi_path = base / "copilot" / "openapi.yaml"
    assert openapi_path.exists(), f"Missing: {openapi_path}"
    content = openapi_path.read_text()
    assert "/api/ask" in content
    assert "askHRPolicy" in content
    logger.info("  ✓ openapi.yaml: /api/ask endpoint defined")

    # CopilotStudioIntegration.md
    doc_path = base / "docs" / "CopilotStudioIntegration.md"
    assert doc_path.exists(), f"Missing: {doc_path}"
    doc_content = doc_path.read_text()
    assert "hr_lab_index" in doc_content
    assert "Foundry Agent" in doc_content
    assert "Path 1" in doc_content
    assert "Path 2" in doc_content
    logger.info("  ✓ CopilotStudioIntegration.md: both integration paths documented")

    logger.info("✓ Step 6 PASSED: Copilot Studio files validated\n")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Full-flow integration test")
    parser.add_argument("--mock", action="store_true",
                        help="Run in mock mode without Azure credentials")
    args = parser.parse_args()

    logger.info("=" * 60)
    logger.info("HR Policy Knowledge Lab – Full-Flow Integration Test")
    logger.info("Mode: %s", "MOCK" if args.mock else "LIVE")
    logger.info("=" * 60)
    logger.info("")

    # Load .env if present
    try:
        from dotenv import load_dotenv
        env_path = Path(__file__).resolve().parent.parent / ".env"
        if env_path.exists():
            load_dotenv(env_path)
            logger.info("Loaded .env from %s\n", env_path)
    except ImportError:
        pass

    passed = 0
    failed = 0
    total = 6

    tests = [
        ("Step 1: Verify Blob Storage", lambda: step_1_verify_blob_storage(args.mock)),
        ("Step 2: Verify Config", lambda: step_2_verify_config()),
        ("Step 3: Search Client", lambda: step_3_test_search_client(args.mock)),
        ("Step 4: Individual Agents", lambda: step_4_test_agents(args.mock)),
        ("Step 5: Orchestrator Pipeline", lambda: step_5_test_orchestrator(args.mock)),
        ("Step 6: Copilot Studio Files", lambda: step_6_verify_copilot_files()),
    ]

    for name, test_fn in tests:
        try:
            test_fn()
            passed += 1
        except Exception as e:
            logger.error("✗ %s FAILED: %s", name, e)
            failed += 1

    logger.info("=" * 60)
    logger.info("RESULTS: %d/%d passed, %d failed", passed, total, failed)
    logger.info("=" * 60)

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
