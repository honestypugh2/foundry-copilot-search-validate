"""
Index Knowledge Base from Blob Storage

Creates or updates the Azure AI Search index (hr_lab_index), sets up an
indexer with the integrated-vectorization skillset, and optionally
provisions the agentic retrieval knowledge source + knowledge base.

The indexer pulls documents from the ``ask-hr-knowledge`` Azure Blob
Storage container and processes them through the skillset pipeline:

    ContentUnderstandingSkill → AzureOpenAIEmbeddingSkill

A MergeSkill definition is included but disabled by default; the
``snippet_with_source`` field is constructed at query time from
``blob_url`` and ``snippet`` instead.  Pass ``--merge-skill`` to
enable the MergeSkill fallback in the skillset pipeline.

Chunks are projected into ``hr_lab_index`` via index projections.

Usage:
    # Full setup: index + indexer + skillset + agentic retrieval
    PYTHONPATH=$PWD/src uv run python src/scripts/index_knowledge_base.py

    # Index + indexer only (skip agentic retrieval provisioning)
    PYTHONPATH=$PWD/src uv run python src/scripts/index_knowledge_base.py --skip-agentic

    # Reset: delete and recreate the indexer
    PYTHONPATH=$PWD/src uv run python src/scripts/index_knowledge_base.py --reset

    # Dry run: validate config without creating resources
    PYTHONPATH=$PWD/src uv run python src/scripts/index_knowledge_base.py --dry-run
"""

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Ensure src is importable
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

logger = logging.getLogger(__name__)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
)

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "search_config.json"


def _load_config() -> dict:
    if not _CONFIG_PATH.exists():
        logger.error("search_config.json not found at %s", _CONFIG_PATH)
        sys.exit(1)
    with open(_CONFIG_PATH) as f:
        return json.load(f)


def _resolve_index_name() -> str:
    """Return the effective index name (env var takes precedence over config)."""
    from search.azure_ai_search_client import AzureAISearchClient
    return AzureAISearchClient().index_name

def _resolve_storage_resource_id(account_name: str) -> str | None:
    """Try to resolve the full ARM resource ID for a storage account via Azure CLI."""
    import subprocess
    try:
        result = subprocess.run(
            [
                "az", "storage", "account", "show",
                "--name", account_name,
                "--query", "id",
                "-o", "tsv",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
        logger.warning(
            "az storage account show failed (rc=%d): %s",
            result.returncode, result.stderr.strip(),
        )
    except FileNotFoundError:
        logger.warning("Azure CLI (az) not found — cannot resolve storage resource ID")
    except subprocess.TimeoutExpired:
        logger.warning("Azure CLI timed out resolving storage resource ID")
    return None


# ---------------------------------------------------------------------------
# Pre-indexing validation — identify oversized documents
# ---------------------------------------------------------------------------

CU_MAX_PAGES = 300  # ContentUnderstandingSkill page limit
_LOGS_DIR = Path(__file__).resolve().parent.parent / "logs"


def validate_blobs(cfg: dict, *, dry_run: bool = False) -> list[str]:
    """Scan blobs and log any that exceed ContentUnderstandingSkill limits.

    Returns a list of blob names that were flagged as oversized so the caller
    can decide how to handle them (skip, use SplitSkill fallback, etc.).
    """
    try:
        from azure.identity import AzureCliCredential
        from azure.storage.blob import ContainerClient
    except ImportError:
        logger.warning("azure-storage-blob not installed — skipping blob validation")
        return []

    resource_id = os.getenv("AZURE_STORAGE_RESOURCE_ID", "")
    account_name = resource_id.rstrip("/").split("/")[-1] if resource_id else ""
    if not account_name:
        storage_url = os.getenv("AZURE_STORAGE_ACCOUNT_URL", "")
        if storage_url:
            import re
            m = re.match(r"https://([^.]+)\.", storage_url)
            account_name = m.group(1) if m else ""
    if not account_name:
        logger.warning("Cannot determine storage account — skipping blob validation")
        return []

    container_name = cfg.get("blob_storage", {}).get("container_name", "ask-hr-knowledge")
    account_url = f"https://{account_name}.blob.core.windows.net"

    try:
        container = ContainerClient(
            account_url=account_url,
            container_name=container_name,
            credential=AzureCliCredential(),
        )
        blobs = list(container.list_blobs(name_starts_with="knowledge_base_lab/"))
    except Exception as e:
        logger.warning("Blob validation failed: %s", e)
        return []

    oversized: list[str] = []
    log_lines: list[str] = []

    for blob in blobs:
        name = blob.name.split("/")[-1]
        ext = name.rsplit(".", 1)[-1].lower() if "." in name else ""

        # Only PDFs can realistically exceed 300 pages at typical sizes.
        # Heuristic: a 300-page PDF with moderate content is ~5 MB+.
        # For exact page count we'd need to download, so we use size as proxy.
        if ext == "pdf" and blob.size > 5_000_000:
            # Download just enough to read page count
            try:
                blob_client = container.get_blob_client(blob.name)
                data = blob_client.download_blob().readall()
                import io

                try:
                    from pypdf import PdfReader
                except ImportError:
                    from PyPDF2 import PdfReader  # type: ignore[no-redef]

                reader = PdfReader(io.BytesIO(data))
                page_count = len(reader.pages)
                if page_count > CU_MAX_PAGES:
                    msg = (
                        f"OVERSIZED: {name} — {page_count} pages "
                        f"(limit {CU_MAX_PAGES}), {blob.size:,} bytes"
                    )
                    logger.warning(msg)
                    log_lines.append(msg)
                    oversized.append(blob.name)
                else:
                    logger.info(
                        "OK: %s — %d pages, %s bytes", name, page_count, f"{blob.size:,}"
                    )
            except Exception as e:
                logger.warning("Could not read page count for %s: %s", name, e)

    # Write log
    _LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = _LOGS_DIR / "skipped_documents.log"
    if log_lines or log_path.exists():
        from datetime import datetime, timezone

        with open(log_path, "a") as f:
            f.write(f"\n--- Validation run {datetime.now(timezone.utc).isoformat()} ---\n")
            if log_lines:
                for line in log_lines:
                    f.write(line + "\n")
            else:
                f.write("No oversized documents found.\n")
        logger.info("Validation log written to %s", log_path)

    if oversized:
        logger.warning(
            "%d document(s) exceed ContentUnderstandingSkill's %d-page limit",
            len(oversized),
            CU_MAX_PAGES,
        )
    else:
        logger.info("All documents within ContentUnderstandingSkill limits")

    return oversized


# ---------------------------------------------------------------------------
# Index creation (delegates to AzureAISearchClient)
# ---------------------------------------------------------------------------

def create_index(cfg: dict, *, dry_run: bool = False) -> bool:
    """Create or update the search index."""
    from search.azure_ai_search_client import AzureAISearchClient

    client = AzureAISearchClient()
    index_name = client.index_name

    if dry_run:
        logger.info("[DRY RUN] Would create/update index '%s'", index_name)
        return True

    logger.info("Creating/updating index '%s' ...", index_name)
    ok = client.create_index()
    if ok:
        logger.info("Index '%s' ready", index_name)
    else:
        logger.error("Failed to create index '%s'", index_name)
    return ok


# ---------------------------------------------------------------------------
# Data source (Blob Storage connection)
# ---------------------------------------------------------------------------

def create_data_source(cfg: dict, *, dry_run: bool = False) -> bool:
    """Create or update the Blob Storage data source connection."""
    try:
        from azure.search.documents.indexes import SearchIndexerClient
        from azure.search.documents.indexes.models import (
            SearchIndexerDataContainer,
            SearchIndexerDataSourceConnection,
        )
    except ImportError:
        logger.error("azure-search-documents SDK not installed")
        return False

    search_endpoint = os.getenv("AZURE_SEARCH_ENDPOINT", "")
    if not search_endpoint:
        logger.error("AZURE_SEARCH_ENDPOINT not set")
        return False

    container_name = cfg.get("blob_storage", {}).get(
        "container_name", "ask-hr-knowledge"
    )
    index_name = _resolve_index_name()
    ds_name = f"{index_name}-datasource".replace("_", "-")

    # Prefer managed identity; fall back to connection string
    storage_conn_str = os.getenv("AZURE_STORAGE_CONNECTION_STRING", "")
    storage_account_url = os.getenv("AZURE_STORAGE_ACCOUNT_URL", "")
    resource_id = os.getenv("AZURE_STORAGE_RESOURCE_ID", "")

    if dry_run:
        logger.info(
            "[DRY RUN] Would create data source '%s' → container '%s'",
            ds_name, container_name,
        )
        return True

    from search.azure_ai_search_client import AzureAISearchClient
    credential = AzureAISearchClient()._get_credential()

    indexer_client = SearchIndexerClient(
        endpoint=search_endpoint, credential=credential,
    )

    # Build connection string for the data source
    if resource_id:
        # Managed identity: use full ARM ResourceId
        conn_str = f"ResourceId={resource_id};"
        logger.info("Using managed identity (ResourceId) for data source")
    elif storage_conn_str:
        conn_str = storage_conn_str
        logger.info("Using connection string for data source")
    elif storage_account_url:
        # Try to resolve the ARM resource ID from the account name
        import re
        m = re.match(r"https://([^.]+)\.blob\.core\.windows\.net", storage_account_url)
        if not m:
            logger.error(
                "Cannot parse account name from AZURE_STORAGE_ACCOUNT_URL=%s",
                storage_account_url,
            )
            return False
        account_name = m.group(1)
        resolved_id = _resolve_storage_resource_id(account_name)
        if resolved_id:
            conn_str = f"ResourceId={resolved_id};"
            logger.info(
                "Resolved storage resource ID for '%s' via Azure CLI", account_name,
            )
        else:
            logger.error(
                "AZURE_STORAGE_ACCOUNT_URL is a blob endpoint, not an ARM resource ID. "
                "Set AZURE_STORAGE_RESOURCE_ID to the full ARM path, e.g.:\n"
                "  /subscriptions/<sub>/resourceGroups/<rg>"
                "/providers/Microsoft.Storage/storageAccounts/%s\n"
                "Or set AZURE_STORAGE_CONNECTION_STRING instead.",
                account_name,
            )
            return False
    else:
        logger.error(
            "No storage credentials. Set AZURE_STORAGE_CONNECTION_STRING, "
            "AZURE_STORAGE_ACCOUNT_URL, or AZURE_STORAGE_RESOURCE_ID."
        )
        return False

    ds = SearchIndexerDataSourceConnection(
        name=ds_name,
        type="azureblob",
        connection_string=conn_str,
        container=SearchIndexerDataContainer(name=container_name),
    )

    try:
        indexer_client.create_or_update_data_source_connection(ds)
        logger.info("Data source '%s' → container '%s' ready", ds_name, container_name)
        return True
    except Exception as e:
        logger.error("Failed to create data source: %s", e)
        return False


# ---------------------------------------------------------------------------
# Skillset
# ---------------------------------------------------------------------------

def create_skillset(cfg: dict, *, dry_run: bool = False) -> bool:
    """Create or update the integrated-vectorization skillset."""
    try:
        from azure.search.documents.indexes import SearchIndexerClient
        from azure.search.documents.indexes.models import (
            AIServicesAccountIdentity,
            AzureOpenAIEmbeddingSkill,
            ContentUnderstandingSkill,
            ContentUnderstandingSkillChunkingProperties,
            InputFieldMappingEntry,
            MergeSkill,
            OutputFieldMappingEntry,
            SearchIndexerIndexProjection,
            SearchIndexerIndexProjectionsParameters,
            SearchIndexerIndexProjectionSelector,
            SearchIndexerSkillset,
        )
    except ImportError:
        logger.error("azure-search-documents indexes models not available")
        return False

    search_endpoint = os.getenv("AZURE_SEARCH_ENDPOINT", "")
    if not search_endpoint:
        logger.error("AZURE_SEARCH_ENDPOINT not set")
        return False

    skillset_cfg = cfg.get("skillset", {})
    skillset_name = skillset_cfg.get("name", "hr-lab-skillset")
    index_name = _resolve_index_name()

    openai_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    # Strip trailing path segments — embedding skill needs just the base URL
    if "/openai" in openai_endpoint:
        openai_endpoint = openai_endpoint.split("/openai")[0]
    openai_key = os.getenv("AZURE_OPENAI_API_KEY", "")

    if dry_run:
        logger.info("[DRY RUN] Would create skillset '%s'", skillset_name)
        return True

    from search.azure_ai_search_client import AzureAISearchClient
    credential = AzureAISearchClient()._get_credential()

    indexer_client = SearchIndexerClient(
        endpoint=search_endpoint, credential=credential,
    )

    # Skill 1: ContentUnderstandingSkill – extract and chunk document content
    chunking_cfg = skillset_cfg.get("chunking", {})
    content_understanding_skill = ContentUnderstandingSkill(
        name="contentUnderstandingSkill",
        description="Extract and chunk document content into text_sections",
        context="/document",
        inputs=[
            InputFieldMappingEntry(name="file_data", source="/document/file_data"),
        ],
        outputs=[
            OutputFieldMappingEntry(name="text_sections", target_name="text_sections"),
        ],
        chunking_properties=ContentUnderstandingSkillChunkingProperties(
            unit="characters",
            maximum_length=chunking_cfg.get("maximum_length", 2000),
            overlap_length=chunking_cfg.get("overlap_length", 200),
        ),
    )

    # Skill 2: MergeSkill – prepend storage path to each chunk for snippet_with_source
    merge_skill = MergeSkill(
        name="mergeSkill",
        description="Prepend storage path to chunk text for snippet_with_source",
        context="/document/text_sections/*",
        inputs=[
            InputFieldMappingEntry(
                name="text",
                source="/document/metadata_storage_path",
            ),
            InputFieldMappingEntry(
                name="itemsToInsert",
                source_context="/document/text_sections/*",
                inputs=[
                    InputFieldMappingEntry(
                        name="chunk",
                        source="/document/text_sections/*",
                    ),
                ],
            ),
        ],
        outputs=[
            OutputFieldMappingEntry(name="mergedText", target_name="snippet_with_source"),
        ],
        insert_pre_tag=" | ",
        insert_post_tag="",
    )

    # Skill 3: AzureOpenAIEmbeddingSkill – generate vectors
    embedding_cfg = next(
        (s for s in skillset_cfg.get("skills", []) if s["type"] == "AzureOpenAIEmbeddingSkill"),
        {},
    )
    embedding_skill = AzureOpenAIEmbeddingSkill(
        name="AzureOpenAIEmbeddingSkill",
        description="Generate text-embedding-3-small vectors",
        resource_url=openai_endpoint,
        deployment_name=embedding_cfg.get("deployment_id", "text-embedding-3-small"),
        model_name=embedding_cfg.get("model_name", "text-embedding-3-small"),
        dimensions=embedding_cfg.get("dimensions", 1536),
        inputs=[
            InputFieldMappingEntry(name="text", source="/document/text_sections/*/content"),
        ],
        outputs=[
            OutputFieldMappingEntry(name="embedding", target_name="text_vector"),
        ],
        context="/document/text_sections/*",
        api_key=openai_key if openai_key and not openai_key.startswith("your_") else None,
    )

    # Index projections
    proj_cfg = skillset_cfg.get("index_projections", {})
    mappings = proj_cfg.get("mappings", [])

    projection_selectors = [
        SearchIndexerIndexProjectionSelector(
            target_index_name=proj_cfg.get("target_index_name", index_name),
            parent_key_field_name=proj_cfg.get("parent_key_field_name", "snippet_parent_id"),
            source_context=proj_cfg.get("source_context", "/document/text_sections/*"),
            mappings=[
                InputFieldMappingEntry(name=m["name"], source=m["source"])
                for m in mappings
            ],
        )
    ]

    index_projections = SearchIndexerIndexProjection(
        selectors=projection_selectors,
        parameters=SearchIndexerIndexProjectionsParameters(
            projection_mode="includeIndexingParentDocuments",
        ),
    )

    # MergeSkill is kept as an optional fallback.  By default we build
    # snippet_with_source at query time from blob_url + snippet, which
    # avoids the StringCollection type constraint issues.
    use_merge = os.getenv("USE_MERGE_SKILL", "").lower() in ("1", "true", "yes")
    if use_merge:
        skills = [content_understanding_skill, merge_skill, embedding_skill]
        logger.info("MergeSkill ENABLED (USE_MERGE_SKILL=%s)", os.getenv("USE_MERGE_SKILL"))
    else:
        skills = [content_understanding_skill, embedding_skill]
        logger.info("MergeSkill disabled — snippet_with_source built at query time")

    # Attach Foundry / AI Services resource for ContentUnderstandingSkill
    # Derive subdomain URL from the OpenAI endpoint host name
    _oai = os.getenv("AZURE_OPENAI_ENDPOINT", "")
    ai_services_url = os.getenv("AZURE_AI_SERVICES_URL", "")
    if not ai_services_url and _oai:
        from urllib.parse import urlparse
        _host = urlparse(_oai).hostname or ""
        _subdomain = _host.split(".")[0]
        ai_services_url = f"https://{_subdomain}.services.ai.azure.com"

    cognitive_services = None
    if ai_services_url:
        cognitive_services = AIServicesAccountIdentity(
            subdomain_url=ai_services_url,
            description="Foundry AI Services for ContentUnderstandingSkill",
        )
        logger.info("AI Services (managed identity): %s", ai_services_url)

    skillset = SearchIndexerSkillset(
        name=skillset_name,
        description="HR Lab integrated vectorization skillset",
        skills=skills,
        index_projection=index_projections,
        cognitive_services_account=cognitive_services,
    )

    try:
        indexer_client.create_or_update_skillset(skillset)
        logger.info("Skillset '%s' ready", skillset_name)
        return True
    except Exception as e:
        logger.error("Failed to create skillset: %s", e)
        return False


# ---------------------------------------------------------------------------
# Indexer
# ---------------------------------------------------------------------------

def create_indexer(
    cfg: dict, *, reset: bool = False, dry_run: bool = False,
) -> bool:
    """Create or update the indexer that connects data source → skillset → index."""
    try:
        from azure.search.documents.indexes import SearchIndexerClient
        from azure.search.documents.indexes.models import (
            FieldMapping,
            FieldMappingFunction,
            IndexingParameters,
            SearchIndexer,
        )
    except ImportError:
        logger.error("azure-search-documents indexes models not available")
        return False

    search_endpoint = os.getenv("AZURE_SEARCH_ENDPOINT", "")
    if not search_endpoint:
        logger.error("AZURE_SEARCH_ENDPOINT not set")
        return False

    index_name = _resolve_index_name()
    skillset_name = cfg.get("skillset", {}).get("name", "hr-lab-skillset")
    ds_name = f"{index_name}-datasource".replace("_", "-")
    indexer_name = f"{index_name}-indexer".replace("_", "-")

    excluded_ext = cfg.get("skillset", {}).get("excluded_extensions", [])

    if dry_run:
        logger.info("[DRY RUN] Would create indexer '%s'", indexer_name)
        return True

    from search.azure_ai_search_client import AzureAISearchClient
    credential = AzureAISearchClient()._get_credential()

    indexer_client = SearchIndexerClient(
        endpoint=search_endpoint, credential=credential,
    )

    if reset:
        try:
            indexer_client.delete_indexer(indexer_name)
            logger.info("Deleted existing indexer '%s'", indexer_name)
        except Exception:
            pass

    # Build indexer configuration
    excluded_ext_str = ",".join(excluded_ext) if excluded_ext else ""

    # Allow indexer to tolerate some failures without entering transientFailure
    max_failed = int(os.getenv("INDEXER_MAX_FAILED_ITEMS", "5"))

    indexer = SearchIndexer(
        name=indexer_name,
        data_source_name=ds_name,
        target_index_name=index_name,
        skillset_name=skillset_name,
        field_mappings=[
            FieldMapping(
                source_field_name="metadata_storage_path",
                target_field_name="metadata_storage_path",
            ),
            FieldMapping(
                source_field_name="metadata_storage_name",
                target_field_name="metadata_storage_name",
            ),
            # Extract policy number (first token before space) from filename
            # e.g. "51350 - Types of Leave_ ... .docx" → "51350"
            FieldMapping(
                source_field_name="metadata_storage_name",
                target_field_name="policy_number",
                mapping_function=FieldMappingFunction(
                    name="extractTokenAtPosition",
                    parameters={"delimiter": " ", "position": 0},
                ),
            ),
            # Strip file extension from filename for parent_title
            # e.g. "51350 - Types of Leave_ ... .docx" → "51350 - Types of Leave_ ... "
            FieldMapping(
                source_field_name="metadata_storage_name",
                target_field_name="parent_title",
                mapping_function=FieldMappingFunction(
                    name="extractTokenAtPosition",
                    parameters={"delimiter": ".", "position": 0},
                ),
            ),
        ],
        parameters=IndexingParameters(
            max_failed_items=max_failed,
            configuration={
                "dataToExtract": "contentAndMetadata",
                "allowSkillsetToReadFileData": True,
                "excludedFileNameExtensions": excluded_ext_str,
            },
        ),
    )

    try:
        indexer_client.create_or_update_indexer(indexer)
        logger.info("Indexer '%s' created/updated", indexer_name)

        # Run the indexer
        logger.info("Running indexer '%s' ...", indexer_name)
        indexer_client.run_indexer(indexer_name)
        logger.info("Indexer '%s' started — check Azure Portal for status", indexer_name)
        return True
    except Exception as e:
        logger.error("Failed to create/run indexer: %s", e)
        return False


# ---------------------------------------------------------------------------
# Agentic retrieval provisioning
# ---------------------------------------------------------------------------

def provision_agentic_retrieval(cfg: dict, *, dry_run: bool = False) -> bool:
    """Create the knowledge source and knowledge base for agentic retrieval."""
    from search.azure_ai_search_client import AzureAISearchClient, AGENTIC_RETRIEVAL_AVAILABLE

    if not AGENTIC_RETRIEVAL_AVAILABLE:
        logger.warning(
            "Agentic retrieval SDK classes not available — skipping. "
            "Install azure-search-documents>=11.7.0b2 with prerelease support."
        )
        return False

    agentic_cfg = cfg.get("agentic_retrieval", {})
    ks_name = agentic_cfg.get("knowledge_source_name", "hr-knowledge-source")
    kb_name = agentic_cfg.get("knowledge_base_name", "hr-knowledge-base")

    if dry_run:
        logger.info(
            "[DRY RUN] Would create knowledge source '%s' and knowledge base '%s'",
            ks_name, kb_name,
        )
        return True

    client = AzureAISearchClient()

    logger.info("Creating knowledge source '%s' ...", ks_name)
    ks_ok = client.create_knowledge_source()
    if not ks_ok:
        return False

    logger.info("Creating knowledge base '%s' ...", kb_name)
    kb_ok = client.create_knowledge_base()
    if not kb_ok:
        return False

    # Step 5b: Create MCP project connection (if project resource ID available)
    logger.info("Creating MCP project connection ...")
    mcp_ok = client.create_project_connection()
    if not mcp_ok:
        logger.warning(
            "MCP project connection not created — set AZURE_AI_PROJECT_RESOURCE_ID "
            "to enable.  Agents can still use the MCP endpoint directly: %s",
            client.get_mcp_endpoint(),
        )
    return True


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description="Index knowledge base from Blob Storage into Azure AI Search",
    )
    parser.add_argument(
        "--skip-agentic",
        action="store_true",
        help="Skip agentic retrieval provisioning (knowledge source + knowledge base)",
    )
    parser.add_argument(
        "--reset",
        action="store_true",
        help="Delete and recreate the indexer",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate config and print what would be created without making changes",
    )
    parser.add_argument(
        "--merge-skill",
        action="store_true",
        help="Enable MergeSkill in the skillset (default: disabled, snippet_with_source built at query time)",
    )
    parser.add_argument(
        "--skip-validation",
        action="store_true",
        help="Skip pre-indexing blob validation (page count check)",
    )
    args = parser.parse_args()

    # Load .env
    env_path = Path(__file__).resolve().parent.parent / ".env"
    if env_path.exists():
        load_dotenv(env_path)
        logger.info("Loaded .env from %s", env_path)
    else:
        load_dotenv()

    # Propagate --merge-skill as env var so create_skillset() can read it
    if args.merge_skill:
        os.environ["USE_MERGE_SKILL"] = "true"

    cfg = _load_config()
    index_name = _resolve_index_name()

    logger.info("=" * 70)
    logger.info("HR Policy Knowledge Lab — Index Knowledge Base from Blob Storage")
    logger.info("  Index:     %s", index_name)
    logger.info("  Container: %s", cfg.get("blob_storage", {}).get("container_name", "ask-hr-knowledge"))
    logger.info("  Skillset:  %s", cfg.get("skillset", {}).get("name", "hr-lab-skillset"))
    logger.info("  Mode:      %s", "DRY RUN" if args.dry_run else "LIVE")
    logger.info("=" * 70)

    # Step 0: Pre-indexing validation — identify oversized documents
    if not args.skip_validation:
        oversized = validate_blobs(cfg, dry_run=args.dry_run)
        if oversized:
            logger.warning(
                "Oversized documents will be skipped by ContentUnderstandingSkill. "
                "The indexer's maxFailedItems is set to tolerate these failures. "
                "Check src/logs/skipped_documents.log for details."
            )
    else:
        logger.info("Skipping pre-indexing blob validation (--skip-validation)")

    # Step 1: Create index
    if not create_index(cfg, dry_run=args.dry_run):
        logger.error("Aborting — index creation failed")
        sys.exit(1)

    # Step 2: Create data source
    if not create_data_source(cfg, dry_run=args.dry_run):
        logger.error("Aborting — data source creation failed")
        sys.exit(1)

    # Step 3: Create skillset
    if not create_skillset(cfg, dry_run=args.dry_run):
        logger.error("Aborting — skillset creation failed")
        sys.exit(1)

    # Step 4: Create and run indexer
    if not create_indexer(cfg, reset=args.reset, dry_run=args.dry_run):
        logger.error("Aborting — indexer creation failed")
        sys.exit(1)

    # Step 5: Provision agentic retrieval
    if not args.skip_agentic:
        if not provision_agentic_retrieval(cfg, dry_run=args.dry_run):
            logger.warning("Agentic retrieval provisioning failed — pipeline will use hybrid search fallback")
    else:
        logger.info("Skipping agentic retrieval provisioning (--skip-agentic)")

    logger.info("=" * 70)
    logger.info("Indexing pipeline setup complete")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()
