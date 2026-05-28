"""
Azure AI Search Client

Wraps the Azure AI Search SDK to provide hybrid search
(full-text + vector + semantic ranker) against the
hr_lab_index populated by the integrated-vectorization
skillset pipeline (ContentUnderstandingSkill → Embedding).
"""

import json
import logging
import os
from pathlib import Path

from typing import Any, Callable, Optional, TypeVar

logger = logging.getLogger(__name__)

_F = TypeVar("_F", bound=Callable[..., Any])

try:
    from azure.core.exceptions import (
        HttpResponseError,
        ServiceRequestError,
        ServiceResponseError,
    )
    AZURE_CORE_EXCEPTIONS_AVAILABLE = True
except ImportError:
    HttpResponseError = ServiceRequestError = ServiceResponseError = Exception  # type: ignore[assignment,misc]
    AZURE_CORE_EXCEPTIONS_AVAILABLE = False

try:
    from tenacity import (
        retry,
        retry_if_exception,
        stop_after_attempt,
        wait_exponential,
        before_sleep_log,
    )

    try:
        from openai import (
            APIConnectionError as _OAIConnErr,
            APITimeoutError as _OAITimeoutErr,
            RateLimitError as _OAIRateErr,
            APIStatusError as _OAIStatusErr,
        )
        _OPENAI_RETRY_TYPES = (_OAIConnErr, _OAITimeoutErr, _OAIRateErr)
        _OPENAI_STATUS_ERR: Optional[type] = _OAIStatusErr
    except ImportError:  # pragma: no cover
        _OPENAI_RETRY_TYPES = ()
        _OPENAI_STATUS_ERR = None

    def _is_transient_search_error(exc: BaseException) -> bool:
        """Return True for retryable Azure SDK / network / OpenAI errors."""
        if isinstance(exc, (ServiceRequestError, ServiceResponseError)):
            return True
        if isinstance(exc, HttpResponseError):
            status = getattr(exc, "status_code", None)
            return status in (408, 429, 500, 502, 503, 504)
        if _OPENAI_RETRY_TYPES and isinstance(exc, _OPENAI_RETRY_TYPES):
            return True
        if _OPENAI_STATUS_ERR is not None and isinstance(exc, _OPENAI_STATUS_ERR):
            status = getattr(exc, "status_code", None)
            return status in (408, 429, 500, 502, 503, 504)
        return False

    search_retry = retry(  # type: ignore[assignment]
        reraise=True,
        stop=stop_after_attempt(4),
        wait=wait_exponential(multiplier=1, min=1, max=10),
        retry=retry_if_exception(_is_transient_search_error),
        before_sleep=before_sleep_log(logger, logging.WARNING),
    )
except ImportError:  # pragma: no cover - tenacity not installed
    logger.warning("tenacity not installed; search calls will not retry")

    def search_retry(fn: _F) -> _F:  # type: ignore[no-redef]
        return fn

try:
    from azure.identity import AzureCliCredential, DefaultAzureCredential
    from azure.search.documents import SearchClient
    from azure.search.documents.models import QueryType, VectorizedQuery
    SEARCH_SDK_AVAILABLE = True
except ImportError:
    SEARCH_SDK_AVAILABLE = False
    logger.warning("azure-search-documents not installed")

try:
    from azure.search.documents.indexes import SearchIndexClient
    from azure.search.documents.indexes.models import (
        AzureOpenAIVectorizerParameters,
        KnowledgeBase,
        KnowledgeBaseAzureOpenAIModel,
        KnowledgeRetrievalLowReasoningEffort,
        KnowledgeRetrievalMediumReasoningEffort,
        KnowledgeRetrievalMinimalReasoningEffort,
        KnowledgeRetrievalOutputMode,
        KnowledgeSourceReference,
        SearchIndexFieldReference,
        SearchIndexKnowledgeSource,
        SearchIndexKnowledgeSourceParameters,
    )
    from azure.search.documents.knowledgebases import KnowledgeBaseRetrievalClient
    from azure.search.documents.knowledgebases.models import (
        KnowledgeBaseMessage,
        KnowledgeBaseMessageTextContent,
        KnowledgeBaseRetrievalRequest,
        KnowledgeRetrievalLowReasoningEffort,
        KnowledgeRetrievalMediumReasoningEffort,
        SearchIndexKnowledgeSourceParams,
    )
    AGENTIC_RETRIEVAL_AVAILABLE = True
except ImportError:
    AGENTIC_RETRIEVAL_AVAILABLE = False
    logger.info("Agentic retrieval classes not available in installed SDK")

try:
    from openai import AzureOpenAI
    OPENAI_AVAILABLE = True
except ImportError:
    OPENAI_AVAILABLE = False
    logger.info("openai not installed, vector search unavailable")

# Load search config
_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "search_config.json"
if _CONFIG_PATH.exists():
    with open(_CONFIG_PATH) as f:
        _FULL_CONFIG = json.load(f)
        _SEARCH_CONFIG = _FULL_CONFIG["search_config"]
        _VECTOR_SEARCH_CONFIG = _FULL_CONFIG.get("vector_search", {})
        _SEMANTIC_SEARCH_CONFIG = _FULL_CONFIG.get("semantic_search", {})
        _AGENTIC_CONFIG = _FULL_CONFIG.get("agentic_retrieval", {})
        _FOUNDRY_AGENT_CONFIG = _FULL_CONFIG.get("foundry_agent", {})
else:
    _FULL_CONFIG = {}
    _SEARCH_CONFIG = {}
    _VECTOR_SEARCH_CONFIG = {}
    _SEMANTIC_SEARCH_CONFIG = {}
    _AGENTIC_CONFIG = {}
    _FOUNDRY_AGENT_CONFIG = {}


class AzureAISearchClient:
    """
    Azure AI Search client for hybrid search against the HR knowledge index.

    Architecture flow:
        Azure Blob Storage  →  Azure AI Search (indexer + skillset)
            →  Azure AI Search Index (2.7M chunks)
            →  Azure AI Foundry Agent (GPT-4.1, answer synthesis)
            →  Copilot Studio / Teams (user interface)
    """

    EMBEDDING_MODEL = "text-embedding-3-small"
    EMBEDDING_DIMENSIONS = 1536

    def __init__(self):
        self.search_endpoint = os.getenv("AZURE_SEARCH_ENDPOINT", "")
        self.index_name = os.getenv(
            "AZURE_SEARCH_INDEX_NAME",
            _SEARCH_CONFIG.get("index_name", "hr_lab_index"),
        )
        self.search_key = os.getenv("AZURE_SEARCH_API_KEY")
        self.use_managed_identity = (
            os.getenv("USE_MANAGED_IDENTITY", "true").lower() == "true"
        )

        # Azure OpenAI for embeddings
        self.openai_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
        self.openai_key = os.getenv("AZURE_OPENAI_API_KEY")
        self.openai_api_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-06-01")

        # Lazy-init clients
        self._search_client: Optional[SearchClient] = None
        self._openai_client = None

        # Index field names from config
        self._vector_field = _SEARCH_CONFIG.get("vector_field", "snippet_vector")
        self._content_field = _SEARCH_CONFIG.get("content_field", "snippet")
        self._source_field = _SEARCH_CONFIG.get("source_field", "snippet_with_source")
        self._blob_url_field = _SEARCH_CONFIG.get("blob_url_field", "blob_url")
        self._filename_field = _SEARCH_CONFIG.get("filename_field", "metadata_storage_name")
        self._filepath_field = _SEARCH_CONFIG.get("filepath_field", "metadata_storage_path")
        self._parent_title_field = _SEARCH_CONFIG.get("parent_title_field", "parent_title")
        self._policy_number_field = _SEARCH_CONFIG.get("policy_number_field", "policy_number")
        self._semantic_config = _SEARCH_CONFIG.get(
            "semantic_configuration", "hr-semantic-config"
        )
        self._top_k = _SEARCH_CONFIG.get("top_k", 5)

    # ------------------------------------------------------------------
    # Field normalization
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_policy_number(value: str) -> str:
        """Return *value* only if it looks like a numeric policy number.

        The indexer's ``extractTokenAtPosition`` grabs the first space-
        delimited token from the blob filename.  For documents whose
        names start with a number (e.g. ``51350 - Types of Leave…``)
        this produces the correct policy number.  For documents without
        a numeric prefix (e.g. ``SOP - Uniform Issuance…``) the token
        is a word like ``SOP`` — not a valid policy number.
        """
        return value if value and value.isdigit() else ""

    # ------------------------------------------------------------------
    # Credential helpers
    # ------------------------------------------------------------------

    def _get_credential(self):
        """Always return a managed-identity / DefaultAzureCredential.

        API key auth (AZURE_SEARCH_API_KEY) is no longer supported by
        this client. The Azure AI Search service should be deployed
        with ``disableLocalAuth=true`` and accessed via RBAC.
        """
        if self.search_key and not self.search_key.startswith("your_"):
            logger.warning(
                "AZURE_SEARCH_API_KEY is set but ignored — using managed identity. "
                "Remove the env var to silence this warning."
            )
        if not self.use_managed_identity:
            logger.warning(
                "USE_MANAGED_IDENTITY=false is no longer supported; forcing MI."
            )
        try:
            return AzureCliCredential()
        except Exception:
            return DefaultAzureCredential()

    # ------------------------------------------------------------------
    # Client accessors
    # ------------------------------------------------------------------

    def _get_search_client(self) -> SearchClient:
        if self._search_client is None:
            if not SEARCH_SDK_AVAILABLE:
                raise RuntimeError("azure-search-documents SDK not installed")
            if not self.search_endpoint:
                raise RuntimeError("AZURE_SEARCH_ENDPOINT not set")
            self._search_client = SearchClient(
                endpoint=self.search_endpoint,
                index_name=self.index_name,
                credential=self._get_credential(),
            )
        return self._search_client

    def _get_openai_client(self):
        if self._openai_client is None and OPENAI_AVAILABLE and self.openai_endpoint:
            if self.openai_key and not self.openai_key.startswith("your_"):
                logger.warning(
                    "AZURE_OPENAI_API_KEY is set but ignored — using managed identity."
                )
            from azure.identity import DefaultAzureCredential, get_bearer_token_provider

            token_provider = get_bearer_token_provider(
                DefaultAzureCredential(),
                "https://cognitiveservices.azure.com/.default",
            )
            self._openai_client = AzureOpenAI(
                azure_endpoint=self.openai_endpoint,
                api_version=self.openai_api_version,
                azure_ad_token_provider=token_provider,
            )
        return self._openai_client

    # ------------------------------------------------------------------
    # Index creation
    # ------------------------------------------------------------------

    def create_index(self) -> bool:
        """Create the HR lab search index with HNSW, scalar quantization, and semantic config."""
        try:
            from azure.search.documents.indexes import SearchIndexClient
            from azure.search.documents.indexes.models import (
                AzureOpenAIVectorizer,
                AzureOpenAIVectorizerParameters,
                HnswAlgorithmConfiguration,
                HnswParameters,
                ScalarQuantizationCompression,
                ScalarQuantizationParameters,
                SearchField,
                SearchFieldDataType,
                SearchIndex,
                SearchableField,
                SemanticConfiguration,
                SemanticField,
                SemanticPrioritizedFields,
                SemanticSearch,
                SimpleField,
                VectorSearch,
                VectorSearchProfile,
            )
        except ImportError:
            logger.error("azure-search-documents indexes models not available")
            return False

        if not self.search_endpoint:
            logger.error("AZURE_SEARCH_ENDPOINT not set")
            return False

        index_client = SearchIndexClient(
            endpoint=self.search_endpoint,
            credential=self._get_credential(),
        )

        # -- Vector search: HNSW + Scalar Quantization --
        algo_cfg = _VECTOR_SEARCH_CONFIG.get("algorithm", {})
        algo_params = algo_cfg.get("parameters", {})
        compression_cfg = _VECTOR_SEARCH_CONFIG.get("compression", {})
        compression_params = compression_cfg.get("parameters", {})
        profile_cfg = _VECTOR_SEARCH_CONFIG.get("profile", {})

        hnsw = HnswAlgorithmConfiguration(
            name=algo_cfg.get("name", "hr-lab-hnsw-config"),
            parameters=HnswParameters(
                metric=algo_params.get("metric", "cosine"),
                m=algo_params.get("m", 4),
                ef_construction=algo_params.get("efConstruction", 400),
                ef_search=algo_params.get("efSearch", 500),
            ),
        )

        scalar_quantization = ScalarQuantizationCompression(
            compression_name=compression_cfg.get("name", "hr-lab-scalar-quantization"),
            parameters=ScalarQuantizationParameters(
                quantized_data_type=compression_params.get("quantized_data_type", "int8"),
            ),
        )

        # -- Query-time vectorizer (AzureOpenAIVectorizer) --
        aoai_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
        if "/openai" in aoai_endpoint:
            aoai_endpoint = aoai_endpoint.split("/openai")[0]
        vectorizer_name = "hr-lab-azure-openai-vectorizer"

        vectorizer = AzureOpenAIVectorizer(
            vectorizer_name=vectorizer_name,
            parameters=AzureOpenAIVectorizerParameters(
                resource_url=aoai_endpoint,
                deployment_name=self.EMBEDDING_MODEL,
                model_name=self.EMBEDDING_MODEL,
            ),
        )

        vector_search = VectorSearch(
            algorithms=[hnsw],
            compressions=[scalar_quantization],
            vectorizers=[vectorizer],
            profiles=[
                VectorSearchProfile(
                    name=profile_cfg.get("name", "hr-lab-vector-profile"),
                    algorithm_configuration_name=algo_cfg.get("name", "hr-lab-hnsw-config"),
                    compression_name=compression_cfg.get("name", "hr-lab-scalar-quantization"),
                    vectorizer_name=vectorizer_name,
                )
            ],
        )

        # -- Semantic search: BoostedRerankerScore on snippet --
        sem_cfg_name = _SEMANTIC_SEARCH_CONFIG.get(
            "configuration_name", self._semantic_config
        )
        sem_content_fields = _SEMANTIC_SEARCH_CONFIG.get(
            "prioritized_fields", {}
        ).get("content_fields", [self._content_field])
        sem_title_field = _SEMANTIC_SEARCH_CONFIG.get(
            "prioritized_fields", {}
        ).get("title_field", None)
        sem_keywords_fields = _SEMANTIC_SEARCH_CONFIG.get(
            "prioritized_fields", {}
        ).get("keywords_fields", [])

        semantic_config = SemanticConfiguration(
            name=sem_cfg_name,
            prioritized_fields=SemanticPrioritizedFields(
                title_field=SemanticField(field_name=sem_title_field) if sem_title_field else None,
                content_fields=[SemanticField(field_name=f) for f in sem_content_fields],
                keywords_fields=[SemanticField(field_name=f) for f in sem_keywords_fields] if sem_keywords_fields else None,
            ),
        )
        semantic_search = SemanticSearch(
            default_configuration_name=sem_cfg_name,
            configurations=[semantic_config],
        )

        # -- Index fields --
        fields = [
            SearchField(name="id", type=SearchFieldDataType.String, key=True, filterable=True, analyzer_name="keyword"),
            SimpleField(name=self._blob_url_field, type=SearchFieldDataType.String),
            SearchableField(name=self._content_field, type=SearchFieldDataType.String),
            SearchableField(name=self._source_field, type=SearchFieldDataType.String),
            SearchableField(name=self._filename_field, type=SearchFieldDataType.String, filterable=True),
            SearchableField(name=self._filepath_field, type=SearchFieldDataType.String, filterable=True),
            SearchableField(name=self._parent_title_field, type=SearchFieldDataType.String, filterable=True),
            SearchableField(name=self._policy_number_field, type=SearchFieldDataType.String, filterable=True),
            SimpleField(
                name=_SEARCH_CONFIG.get("parent_key_field", "snippet_parent_id"),
                type=SearchFieldDataType.String,
                filterable=True,
            ),
            SearchField(
                name=self._vector_field,
                type=SearchFieldDataType.Collection(SearchFieldDataType.Single),
                searchable=True,
                vector_search_dimensions=self.EMBEDDING_DIMENSIONS,
                vector_search_profile_name=profile_cfg.get("name", "hr-lab-vector-profile"),
            ),
        ]

        index = SearchIndex(
            name=self.index_name,
            fields=fields,
            vector_search=vector_search,
            semantic_search=semantic_search,
        )

        try:
            index_client.create_or_update_index(index)
            logger.info(f"Index '{self.index_name}' created/updated")
            return True
        except Exception as e:
            logger.error(f"Failed to create index: {e}")
            return False

    # ------------------------------------------------------------------
    # Embedding
    # ------------------------------------------------------------------

    def generate_embedding(self, text: str) -> list[float] | None:
        """Generate embedding vector using text-embedding-3-small.

        Wraps the OpenAI call with retry/backoff on transient errors.
        Returns ``None`` only on non-transient failures (e.g. auth).
        """
        client = self._get_openai_client()
        if not client:
            return None

        @search_retry
        def _call() -> list[float]:
            response = client.embeddings.create(
                input=text, model=self.EMBEDDING_MODEL
            )
            return response.data[0].embedding

        try:
            return _call()
        except Exception as e:
            logger.warning(f"Embedding generation failed after retries: {e}")
            return None

    # ------------------------------------------------------------------
    # Hybrid search
    # ------------------------------------------------------------------

    @search_retry
    def hybrid_search(
        self, user_query: str, embedding: list[float] | None = None, top: int | None = None
    ) -> list[dict[str, Any]]:
        """
        Execute hybrid search: full-text + vector + semantic ranker.

        Args:
            user_query: The user's natural-language query.
            embedding: Pre-computed embedding vector.  If *None* one will
                       be generated from *user_query*.
            top: Number of results to return (defaults to config top_k).

        Returns:
            List of hit dicts with keys: content, filePath, documentType,
            container, score, blob_url.
        """
        top = top or self._top_k
        search_client = self._get_search_client()

        search_kwargs: dict[str, Any] = {
            "search_text": user_query,
            "top": top,
            "include_total_count": True,
            "query_type": QueryType.SEMANTIC,
            "semantic_configuration_name": self._semantic_config,
        }

        # Vector leg
        vec = embedding or self.generate_embedding(user_query)
        if vec:
            search_kwargs["vector_queries"] = [
                VectorizedQuery(
                    vector=vec,
                    k_nearest_neighbors=top,
                    fields=self._vector_field,
                )
            ]
            logger.info("Using hybrid search (text + vector + semantic ranker)")
        else:
            logger.info("Using text search + semantic ranker (no embedding)")

        try:
            results = search_client.search(**search_kwargs)

            hits: list[dict[str, Any]] = []
            for result in results:
                content = result.get(self._content_field, "")
                blob_url = result.get(self._blob_url_field, "")
                # Prefer index-time snippet_with_source; fall back to
                # query-time construction from blob_url + snippet.
                source = result.get(self._source_field, "") or ""
                if not source and blob_url and content:
                    source = f"{blob_url} | {content}"
                hits.append(
                    {
                        "content": content,
                        "source": source,
                        "filePath": result.get(self._filepath_field, result.get("metadata_storage_path", "")),
                        "fileName": result.get(self._filename_field, result.get("metadata_storage_name", "")),
                        "parentTitle": result.get(self._parent_title_field, ""),
                        "policyNumber": self._normalize_policy_number(
                            result.get(self._policy_number_field, ""),
                        ),
                        "documentType": result.get("metadata_content_type", result.get("documentType", "")),
                        "container": result.get("metadata_storage_container_name", "ask-hr-knowledge"),
                        "score": result.get("@search.score", 0),
                        "reranker_score": result.get("@search.reranker_score"),
                        "blob_url": blob_url,
                    }
                )

            logger.info(f"Hybrid search returned {len(hits)} results")
            return hits

        except Exception as e:
            # Re-raise transient azure-core errors so @search_retry can apply
            # backoff; swallow everything else and return an empty list so the
            # caller can degrade gracefully.
            if AZURE_CORE_EXCEPTIONS_AVAILABLE and isinstance(
                e, (ServiceRequestError, ServiceResponseError, HttpResponseError)
            ):
                raise
            logger.error(f"Hybrid search failed: {e}")
            return []

    # ------------------------------------------------------------------
    # Agentic Retrieval – Knowledge Source & Knowledge Base
    # ------------------------------------------------------------------

    def create_knowledge_source(self) -> bool:
        """Create or update the knowledge source pointing at hr_lab_index."""
        if not AGENTIC_RETRIEVAL_AVAILABLE:
            logger.error("Agentic retrieval SDK classes not available")
            return False

        if not self.search_endpoint:
            logger.error("AZURE_SEARCH_ENDPOINT not set")
            return False

        ks_name = _AGENTIC_CONFIG.get("knowledge_source_name", "hr-knowledge-source")
        source_fields = _AGENTIC_CONFIG.get("source_data_fields", [
            "id", "snippet", "metadata_storage_name",
            "metadata_storage_path", "parent_title", "policy_number",
        ])
        search_fields = _AGENTIC_CONFIG.get("search_fields", [
            "snippet", "parent_title", "policy_number",
        ])
        semantic_config = _AGENTIC_CONFIG.get(
            "semantic_configuration_name",
            _SEARCH_CONFIG.get("semantic_configuration", ""),
        )

        try:
            index_client = SearchIndexClient(
                endpoint=self.search_endpoint,
                credential=self._get_credential(),
            )

            ks = SearchIndexKnowledgeSource(
                name=ks_name,
                description=_AGENTIC_CONFIG.get(
                    "knowledge_source_description",
                    "Knowledge source for HR policy documents",
                ),
                search_index_parameters=SearchIndexKnowledgeSourceParameters(
                    search_index_name=self.index_name,
                    source_data_fields=[
                        SearchIndexFieldReference(name=f) for f in source_fields
                    ],
                    search_fields=[
                        SearchIndexFieldReference(name=f) for f in search_fields
                    ],
                    semantic_configuration_name=semantic_config or None,
                ),
            )

            index_client.create_or_update_knowledge_source(knowledge_source=ks)
            logger.info("Knowledge source '%s' created/updated", ks_name)
            return True

        except Exception as e:
            logger.error("Failed to create knowledge source: %s", e)
            return False

    def create_knowledge_base(self) -> bool:
        """Create or update the knowledge base with LLM config and answer synthesis."""
        if not AGENTIC_RETRIEVAL_AVAILABLE:
            logger.error("Agentic retrieval SDK classes not available")
            return False

        if not self.search_endpoint:
            logger.error("AZURE_SEARCH_ENDPOINT not set")
            return False

        kb_name = _AGENTIC_CONFIG.get("knowledge_base_name", "hr-knowledge-base")
        ks_name = _AGENTIC_CONFIG.get("knowledge_source_name", "hr-knowledge-source")
        output_mode_str = _AGENTIC_CONFIG.get("output_mode", "ANSWER_SYNTHESIS")

        aoai_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
        # Strip trailing path segments — resource_url needs just the base URL
        if "/openai" in aoai_endpoint:
            aoai_endpoint = aoai_endpoint.split("/openai")[0]
        gpt_deployment = os.getenv(
            "AZURE_OPENAI_GPT_DEPLOYMENT",
            os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4.1"),
        )
        gpt_model = os.getenv("AZURE_OPENAI_GPT_MODEL", "gpt-4.1")
        answer_instructions = _FOUNDRY_AGENT_CONFIG.get("answer_instructions", "")
        retrieval_instructions = _FOUNDRY_AGENT_CONFIG.get("retrieval_instructions", "")

        # Inject mandatory file-location instruction into answer_instructions
        # so the KB's internal LLM preserves metadata_storage_path in answers
        location_instruction = (
            "\nCRITICAL: When the user asks for a file location, path, URL, or "
            "where a document is stored, you MUST include the exact "
            "metadata_storage_path and blob_url values from the source data "
            "in your response. Never say the path was not provided if it "
            "exists in the source_data fields."
        )
        if answer_instructions and "metadata_storage_path" not in answer_instructions:
            answer_instructions += location_instruction
        elif not answer_instructions:
            answer_instructions = location_instruction.strip()

        # Resolve reasoning effort from config
        reasoning_effort_str = _AGENTIC_CONFIG.get(
            "retrieval_reasoning_effort",
            _FOUNDRY_AGENT_CONFIG.get("retrieval_reasoning", "medium"),
        )
        reasoning_effort_map = {
            "low": KnowledgeRetrievalLowReasoningEffort,
            "medium": KnowledgeRetrievalMediumReasoningEffort,
            "minimal": KnowledgeRetrievalMinimalReasoningEffort,
        }
        reasoning_effort_cls = reasoning_effort_map.get(
            reasoning_effort_str.lower(), KnowledgeRetrievalMediumReasoningEffort
        )
        reasoning_effort = reasoning_effort_cls()

        try:
            index_client = SearchIndexClient(
                endpoint=self.search_endpoint,
                credential=self._get_credential(),
            )

            aoai_params = AzureOpenAIVectorizerParameters(
                resource_url=aoai_endpoint,
                deployment_name=gpt_deployment,
                model_name=gpt_model,
            )

            output_mode = (
                KnowledgeRetrievalOutputMode.EXTRACTIVE_DATA
                if output_mode_str.upper() in ("EXTRACTIVE_DATA", "EXTRACTIVE")
                else KnowledgeRetrievalOutputMode.ANSWER_SYNTHESIS
            )

            knowledge_base = KnowledgeBase(
                name=kb_name,
                models=[
                    KnowledgeBaseAzureOpenAIModel(
                        azure_open_ai_parameters=aoai_params,
                    )
                ],  # type: ignore[arg-type]
                knowledge_sources=[
                    KnowledgeSourceReference(name=ks_name),
                ],
                output_mode=output_mode,
                retrieval_reasoning_effort=reasoning_effort,
                retrieval_instructions=retrieval_instructions,
                answer_instructions=answer_instructions,
            )

            index_client.create_or_update_knowledge_base(knowledge_base)
            logger.info("Knowledge base '%s' created/updated", kb_name)

            # Build and store MCP endpoint for agent integration
            mcp_cfg = _AGENTIC_CONFIG.get("mcp", {})
            api_version = mcp_cfg.get("api_version", "2025-11-01-Preview")
            self.mcp_endpoint = (
                f"{self.search_endpoint}/knowledgebases/{kb_name}"
                f"/mcp?api-version={api_version}"
            )
            logger.info("MCP endpoint: %s", self.mcp_endpoint)
            return True

        except Exception as e:
            logger.error("Failed to create knowledge base: %s", e)
            return False

    def get_mcp_endpoint(self) -> str:
        """Return the MCP endpoint URL for the knowledge base."""
        if hasattr(self, "mcp_endpoint") and self.mcp_endpoint:
            return self.mcp_endpoint
        kb_name = _AGENTIC_CONFIG.get("knowledge_base_name", "hr-knowledge-base")
        mcp_cfg = _AGENTIC_CONFIG.get("mcp", {})
        api_version = mcp_cfg.get("api_version", "2025-11-01-Preview")
        return (
            f"{self.search_endpoint}/knowledgebases/{kb_name}"
            f"/mcp?api-version={api_version}"
        )

    def create_project_connection(self) -> bool:
        """Create the MCP project connection in Microsoft Foundry.

        Uses the Azure Management API to create a ``RemoteTool``
        connection from the Foundry project to the knowledge base's
        MCP endpoint.  This enables agents to use ``MCPTool`` for
        retrieval.
        """
        import requests
        from azure.identity import DefaultAzureCredential, get_bearer_token_provider

        project_resource_id = os.getenv("AZURE_AI_PROJECT_RESOURCE_ID", "")
        if not project_resource_id:
            logger.warning(
                "AZURE_AI_PROJECT_RESOURCE_ID not set — skipping MCP project connection"
            )
            return False

        mcp_cfg = _AGENTIC_CONFIG.get("mcp", {})
        connection_name = mcp_cfg.get(
            "project_connection_name", "hr-knowledge-mcp-connection"
        )
        mcp_endpoint = self.get_mcp_endpoint()

        try:
            credential = DefaultAzureCredential()
            token_provider = get_bearer_token_provider(
                credential, "https://management.azure.com/.default"
            )

            response = requests.put(
                f"https://management.azure.com{project_resource_id}"
                f"/connections/{connection_name}?api-version=2025-10-01-preview",
                headers={"Authorization": f"Bearer {token_provider()}"},
                json={
                    "name": connection_name,
                    "type": "Microsoft.MachineLearningServices/workspaces/connections",
                    "properties": {
                        "authType": "ProjectManagedIdentity",
                        "category": "RemoteTool",
                        "target": mcp_endpoint,
                        "isSharedToAll": True,
                        "audience": "https://search.azure.com/",
                        "metadata": {"ApiType": "Azure"},
                    },
                },
                timeout=30,
            )
            response.raise_for_status()
            logger.info(
                "MCP project connection '%s' created/updated", connection_name
            )
            return True

        except Exception as e:
            logger.error("Failed to create MCP project connection: %s", e)
            return False

    @search_retry
    def agentic_retrieve(
        self,
        messages: list[dict[str, str]],
    ) -> dict[str, Any]:
        """
        Execute agentic retrieval against the HR knowledge base.

        Sends conversation messages to the knowledge base, which
        decomposes queries into subqueries, runs them against the
        knowledge source, reranks results, and synthesizes an answer.

        Args:
            messages: Conversation history as list of
                      {"role": "user"|"assistant", "content": "..."}.

        Returns:
            Dict with "response" (synthesized answer), "matches"
            (normalised references), and "activity" (query plan).
        """
        if not AGENTIC_RETRIEVAL_AVAILABLE:
            raise RuntimeError("Agentic retrieval SDK classes not available")

        if not self.search_endpoint:
            raise RuntimeError("AZURE_SEARCH_ENDPOINT not set")

        kb_name = _AGENTIC_CONFIG.get("knowledge_base_name", "hr-knowledge-base")
        ks_name = _AGENTIC_CONFIG.get("knowledge_source_name", "hr-knowledge-source")

        reasoning_effort_str = _AGENTIC_CONFIG.get(
            "retrieval_reasoning_effort",
            _FOUNDRY_AGENT_CONFIG.get("retrieval_reasoning", "medium"),
        )
        reasoning_effort = (
            KnowledgeRetrievalLowReasoningEffort()
            if reasoning_effort_str == "low"
            else KnowledgeRetrievalMediumReasoningEffort()
        )

        include_refs = _AGENTIC_CONFIG.get("include_references", True)
        include_ref_data = _AGENTIC_CONFIG.get("include_reference_source_data", True)
        always_query = _AGENTIC_CONFIG.get("always_query_source", True)
        include_activity = _AGENTIC_CONFIG.get("include_activity", True)

        agent_client = KnowledgeBaseRetrievalClient(
            endpoint=self.search_endpoint,
            knowledge_base_name=kb_name,
            credential=self._get_credential(),
        )

        kb_messages = [
            KnowledgeBaseMessage(
                role=m["role"],
                content=[KnowledgeBaseMessageTextContent(text=m["content"])],  # type: ignore[arg-type]
            )
            for m in messages
            if m["role"] != "system"
        ]

        req = KnowledgeBaseRetrievalRequest(
            messages=kb_messages,
            knowledge_source_params=[
                SearchIndexKnowledgeSourceParams(
                    knowledge_source_name=ks_name,
                    include_references=include_refs,
                    include_reference_source_data=include_ref_data,
                    always_query_source=always_query,
                )
            ],  # type: ignore[arg-type]
            include_activity=include_activity,
            retrieval_reasoning_effort=reasoning_effort,
        )

        result = agent_client.retrieve(retrieval_request=req)

        # -- Parse response ---------------------------------------------------
        response_text = ""
        if result.response:
            parts = []
            for resp in result.response:
                for content_item in resp.content:
                    content_dict = content_item.as_dict() if hasattr(content_item, "as_dict") else content_item
                    if isinstance(content_dict, dict):
                        parts.append(content_dict.get("text", ""))
                    elif hasattr(content_item, "text"):
                        parts.append(content_item.text)  # type: ignore[attr-defined]
            response_text = "\n\n".join(parts) if parts else ""

        # -- Parse references → normalised matches ---------------------------
        matches: list[dict[str, Any]] = []
        if result.references:
            for ref in result.references:
                ref_dict: dict[str, Any] = ref.as_dict() if hasattr(ref, "as_dict") else {}  # type: ignore[assignment]
                src: dict[str, Any] = ref_dict.get("source_data", {}) or {}
                matches.append({
                    "content": src.get("snippet", src.get("page_chunk", "")),
                    "filePath": src.get("metadata_storage_path", ""),
                    "fileName": src.get("metadata_storage_name", ""),
                    "parentTitle": src.get("parent_title", ""),
                    "policyNumber": self._normalize_policy_number(
                        src.get("policy_number", ""),
                    ),
                    "documentType": "",
                    "container": "ask-hr-knowledge",
                    "reranker_score": ref_dict.get("reranker_score"),
                    "blob_url": src.get("blob_url", ""),
                    "ref_id": ref_dict.get("id", ""),
                })

        # -- Parse activity ----------------------------------------------------
        activity: list[Any] = []
        if result.activity:
            activity = [
                a.as_dict() if hasattr(a, "as_dict") else a
                for a in result.activity
            ]

        logger.info(
            "Agentic retrieval: %d references, activity_steps=%d",
            len(matches),
            len(activity),
        )

        return {
            "response": response_text,
            "matches": matches,
            "activity": activity,
        }
