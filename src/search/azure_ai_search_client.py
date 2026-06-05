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

# Knowledge Source / Knowledge Base management uses direct REST against the
# GA agentic-retrieval surface (api-version 2026-04-01). The GA Python SDK
# (azure-search-documents 12.0.0) still ships preview-era models on the
# request types (e.g. KnowledgeBaseRetrievalRequest.messages was removed),
# so we call REST directly here. The SDK is still used for index creation
# and runtime hybrid search via SearchIndexClient/SearchClient.
try:
    import requests
    from azure.identity import get_bearer_token_provider
    from azure.search.documents.indexes import SearchIndexClient  # noqa: F401
    AGENTIC_RETRIEVAL_AVAILABLE = True
except ImportError:
    AGENTIC_RETRIEVAL_AVAILABLE = False
    logger.info(
        "requests / azure-identity / azure-search-documents not installed; "
        "agentic retrieval REST helpers disabled"
    )

# GA agentic-retrieval REST contract (api-version 2026-04-01) does NOT
# accept retrievalReasoningEffort, output_mode, answer_instructions,
# retrieval_instructions, or message history. Those preview-only knobs are
# expressed on the calling Foundry agent instead. We keep these constants
# documented here as the contract boundary.
KB_REST_API_VERSION = "2026-04-01"
KB_REST_TIMEOUT_SECONDS = 60
SEARCH_DATA_PLANE_SCOPE = "https://search.azure.com/.default"

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

    def _get_search_bearer_token(self) -> str:
        """Return an AAD bearer token for the Azure AI Search data plane."""
        token_provider = get_bearer_token_provider(
            self._get_credential(), SEARCH_DATA_PLANE_SCOPE
        )
        return token_provider()

    def create_knowledge_source(self) -> bool:
        """Create or update the searchIndex knowledge source via REST 2026-04-01.

        Preserves the agentic-retrieval components from the beta version:
        - kind: ``searchIndex`` (references an existing index)
        - ``searchIndexName`` (target index)
        - ``sourceDataFields`` (fields surfaced in references)
        - ``searchFields`` (fields used for retrieval)
        - ``semanticConfigurationName`` (semantic ranker)
        """
        if not AGENTIC_RETRIEVAL_AVAILABLE:
            logger.error("Agentic retrieval REST helpers unavailable (missing deps)")
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
        description = _AGENTIC_CONFIG.get(
            "knowledge_source_description",
            "Knowledge source for HR policy documents",
        )

        body: dict[str, Any] = {
            "name": ks_name,
            "description": description,
            "kind": "searchIndex",
            "searchIndexParameters": {
                "searchIndexName": self.index_name,
                "sourceDataFields": [{"name": f} for f in source_fields],
                "searchFields": [{"name": f} for f in search_fields],
            },
        }
        if semantic_config:
            body["searchIndexParameters"]["semanticConfigurationName"] = semantic_config

        url = (
            f"{self.search_endpoint.rstrip('/')}/knowledgesources/{ks_name}"
            f"?api-version={KB_REST_API_VERSION}"
        )

        try:
            token = self._get_search_bearer_token()
            response = requests.put(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=KB_REST_TIMEOUT_SECONDS,
            )
            if response.status_code >= 400:
                logger.error(
                    "Knowledge source PUT failed (%s): %s",
                    response.status_code, response.text,
                )
                return False
            logger.info("Knowledge source '%s' created/updated via REST %s",
                        ks_name, KB_REST_API_VERSION)
            return True
        except Exception as e:
            logger.error("Failed to create knowledge source: %s", e)
            return False

    def create_knowledge_base(self) -> bool:
        """Create or update the knowledge base via REST 2026-04-01 (GA).

        GA contract drops preview-era ``answer_instructions``,
        ``retrieval_instructions``, ``output_mode``, and the preview
        reasoning-effort knobs from the request body (those move onto
        the calling Foundry agent's system prompt).

        ``models`` is REQUIRED whenever the Knowledge Base / MCP
        transport will use any reasoning effort above ``Minimal``. The
        MCP transport defaults to a non-Minimal effort, so we always
        include the model. The model deployment defaults to
        ``gpt-4.1-mini`` (overridable via ``AZURE_OPENAI_GPT_DEPLOYMENT``).
        """
        if not AGENTIC_RETRIEVAL_AVAILABLE:
            logger.error("Agentic retrieval REST helpers unavailable (missing deps)")
            return False

        if not self.search_endpoint:
            logger.error("AZURE_SEARCH_ENDPOINT not set")
            return False

        kb_name = _AGENTIC_CONFIG.get("knowledge_base_name", "hr-knowledge-base")
        ks_name = _AGENTIC_CONFIG.get("knowledge_source_name", "hr-knowledge-source")
        description = _AGENTIC_CONFIG.get(
            "knowledge_base_description",
            "HR policy knowledge base for agentic retrieval",
        )

        # Model for the KB itself (required by MCP for non-Minimal reasoning).
        # Default deployment is gpt-4.1-mini; AZURE_OPENAI_GPT_DEPLOYMENT
        # overrides. The Foundry agent's own model (used for synthesis) is
        # controlled separately via AZURE_AI_MODEL_DEPLOYMENT_NAME.
        aoai_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT", "")
        if "/openai" in aoai_endpoint:
            aoai_endpoint = aoai_endpoint.split("/openai")[0]
        gpt_deployment = os.getenv(
            "AZURE_OPENAI_GPT_DEPLOYMENT",
            os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4.1-mini"),
        )
        gpt_model = os.getenv("AZURE_OPENAI_GPT_MODEL", gpt_deployment)

        body = {
            "name": kb_name,
            "description": description,
            "knowledgeSources": [{"name": ks_name}],
            "models": [
                {
                    "kind": "azureOpenAI",
                    "azureOpenAIParameters": {
                        "resourceUri": aoai_endpoint,
                        "deploymentId": gpt_deployment,
                        "modelName": gpt_model,
                    },
                }
            ],
        }

        url = (
            f"{self.search_endpoint.rstrip('/')}/knowledgebases/{kb_name}"
            f"?api-version={KB_REST_API_VERSION}"
        )

        try:
            token = self._get_search_bearer_token()
            response = requests.put(
                url,
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                json=body,
                timeout=KB_REST_TIMEOUT_SECONDS,
            )
            if response.status_code >= 400:
                logger.error(
                    "Knowledge base PUT failed (%s): %s",
                    response.status_code, response.text,
                )
                return False
            logger.info(
                "Knowledge base '%s' created/updated via REST %s (model=%s)",
                kb_name, KB_REST_API_VERSION, gpt_deployment,
            )

            # MCP endpoint remains on its preview api-version (the MCP
            # transport is separate from the KB management api-version).
            mcp_cfg = _AGENTIC_CONFIG.get("mcp", {})
            api_version = mcp_cfg.get("api_version", "2025-11-01-Preview")
            self.mcp_endpoint = (
                f"{self.search_endpoint.rstrip('/')}/knowledgebases/{kb_name}"
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
        messages: list[dict[str, str]] | None = None,
        *,
        query: str | None = None,
    ) -> dict[str, Any]:
        """Execute agentic retrieval via REST 2026-04-01 (GA).

        GA REST replaces the preview ``messages`` array with a single
        ``intents`` array per turn. The HR application (Foundry agent +
        MCPTool) is the actual production hot path; this helper exists
        for direct REST testing and parity with the beta version's
        components:
        - ``intents[]`` with ``type: "semantic"`` and ``search`` text
        - ``knowledgeSourceParams[]`` with reranker threshold and
          include-references flags (kept from the beta version)
        - Returns ``references`` (extractive grounding) and ``activity``
          (query-planning trace) — the calling agent does synthesis.

        Args:
            messages: Optional conversation history. The last user
                message's content becomes the retrieval ``intent.search``.
                Provided for backward compatibility with beta callers.
            query: Explicit query string. Overrides ``messages`` when set.

        Returns:
            Dict with ``response`` (joined extractive snippets),
            ``matches`` (normalised references), and ``activity`` (the
            query plan emitted by the service).
        """
        if not AGENTIC_RETRIEVAL_AVAILABLE:
            raise RuntimeError("Agentic retrieval REST helpers unavailable (missing deps)")

        if not self.search_endpoint:
            raise RuntimeError("AZURE_SEARCH_ENDPOINT not set")

        # Resolve the search text from explicit query or last user message
        search_text = (query or "").strip()
        if not search_text and messages:
            for msg in reversed(messages):
                if msg.get("role") == "user" and msg.get("content"):
                    search_text = msg["content"].strip()
                    break
        if not search_text:
            raise ValueError("agentic_retrieve requires a non-empty query or user message")

        kb_name = _AGENTIC_CONFIG.get("knowledge_base_name", "hr-knowledge-base")
        ks_name = _AGENTIC_CONFIG.get("knowledge_source_name", "hr-knowledge-source")

        include_refs = _AGENTIC_CONFIG.get("include_references", True)
        include_ref_data = _AGENTIC_CONFIG.get("include_reference_source_data", True)
        reranker_threshold = _AGENTIC_CONFIG.get("reranker_threshold", 2.5)
        max_runtime = _AGENTIC_CONFIG.get("max_runtime_in_seconds", 30)
        max_output_tokens = _AGENTIC_CONFIG.get("max_output_size_in_tokens", 6000)

        body: dict[str, Any] = {
            "intents": [
                {"type": "semantic", "search": search_text}
            ],
            "knowledgeSourceParams": [
                {
                    "knowledgeSourceName": ks_name,
                    "kind": "searchIndex",
                    "includeReferences": include_refs,
                    "includeReferenceSourceData": include_ref_data,
                    "rerankerThreshold": reranker_threshold,
                }
            ],
            "maxRuntimeInSeconds": max_runtime,
            "maxOutputSizeInTokens": max_output_tokens,
        }

        url = (
            f"{self.search_endpoint.rstrip('/')}/knowledgebases/{kb_name}/retrieve"
            f"?api-version={KB_REST_API_VERSION}"
        )

        token = self._get_search_bearer_token()
        response = requests.post(
            url,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=body,
            timeout=KB_REST_TIMEOUT_SECONDS,
        )
        if response.status_code >= 400:
            logger.error(
                "Agentic retrieve failed (%s): %s",
                response.status_code, response.text,
            )
            response.raise_for_status()

        result = response.json() or {}

        # -- Parse references → normalised matches ---------------------------
        matches: list[dict[str, Any]] = []
        for ref in result.get("references", []) or []:
            src = ref.get("sourceData") or ref.get("source_data") or {}
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
                "reranker_score": ref.get("rerankerScore") or ref.get("reranker_score"),
                "blob_url": src.get("blob_url", ""),
                "ref_id": ref.get("id", ""),
            })

        # -- Build response text from extractive snippets --------------------
        response_text = "\n\n".join(
            m["content"] for m in matches if m.get("content")
        )

        activity = result.get("activity", []) or []

        logger.info(
            "Agentic retrieval (REST %s): %d references, activity_steps=%d",
            KB_REST_API_VERSION, len(matches), len(activity),
        )

        return {
            "response": response_text,
            "matches": matches,
            "activity": activity,
        }
