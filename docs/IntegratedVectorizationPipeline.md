# Integrated Vectorization Pipeline

This document describes the end-to-end integrated vectorization pipeline that indexes HR policy documents from Azure Blob Storage into Azure AI Search, including field extraction, chunking, embedding, and agentic retrieval provisioning.

## Pipeline Overview

```
Azure Blob Storage               Azure AI Search Indexer
┌──────────────────────┐    ┌──────────────────────────────────────────────┐
│ ask-hr-knowledge     │    │  field_mappings (pre-skillset)                │
│  └─ knowledge_base_lab/ ──►  metadata_storage_name → metadata_storage_name│
│       ├─ 51350 - …  │    │  metadata_storage_path → metadata_storage_path│
│       ├─ 50715 - …  │    │  metadata_storage_name → policy_number            (extractTokenAtPosition " " pos 0) │
│       └─ …           │    │  metadata_storage_name → parent_title              (extractTokenAtPosition "." pos 0) │
└──────────────────────┘    └────────────────────┬─────────────────────────┘
                                                 │
                                                 ▼
                            ┌──────────────────────────────────────────────┐
                            │  Skillset: hr-lab-skillset                   │
                            │                                              │
                            │  1. ContentUnderstandingSkill                 │
                            │     /document/file_data → /document/text_sections│
                            │     (chunking: 2000 chars, 200 overlap)      │
                            │                                              │
                            │  2. AzureOpenAIEmbeddingSkill                │
                            │     text_sections/*/content → text_vector    │
                            │     (text-embedding-3-small, 1536 dims)      │
                            │                                              │
                            │  3. MergeSkill (optional, disabled by default)│
                            │     blob_url + chunk → snippet_with_source   │
                            └────────────────────┬─────────────────────────┘
                                                 │
                                                 ▼
                            ┌──────────────────────────────────────────────┐
                            │  Index Projections → hr_lab_index            │
                            │  source_context: /document/text_sections/*   │
                            │  projection_mode: includeIndexingParentDocuments│
                            │                                              │
                            │  Chunk fields:                               │
                            │    snippet_vector  ← text_sections/*/text_vector│
                            │    snippet         ← text_sections/*/content │
                            │    snippet_with_source ← text_sections/*/snippet_with_source│
                            │                                              │
                            │  Document-level fields:                      │
                            │    blob_url        ← /document/metadata_storage_path│
                            │    metadata_storage_name ← /document/metadata_storage_name│
                            │    metadata_storage_path ← /document/metadata_storage_path│
                            │    parent_title    ← /document/parent_title│
                            │    policy_number   ← /document/policy_number│
                            └────────────────────┬─────────────────────────┘
                                                 │
                                                 ▼
                            ┌──────────────────────────────────────────────┐
                            │  Agentic Retrieval (optional)                │
                            │  knowledge source: hr-knowledge-source       │
                            │  knowledge base:   hr-knowledge-base         │
                            │  output_mode:      ANSWER_SYNTHESIS          │
                            └──────────────────────────────────────────────┘
```

## Script Execution

```bash
# Full setup: index + data source + skillset + indexer + agentic retrieval
PYTHONPATH=$PWD/src uv run python src/scripts/index_knowledge_base.py

# Skip agentic retrieval provisioning
PYTHONPATH=$PWD/src uv run python src/scripts/index_knowledge_base.py --skip-agentic

# Reset indexer (delete and recreate)
PYTHONPATH=$PWD/src uv run python src/scripts/index_knowledge_base.py --reset

# Dry run — validate config without creating resources
PYTHONPATH=$PWD/src uv run python src/scripts/index_knowledge_base.py --dry-run

# Enable MergeSkill (disabled by default)
PYTHONPATH=$PWD/src uv run python src/scripts/index_knowledge_base.py --merge-skill
```

## Pipeline Steps

### Step 0 — Pre-indexing Blob Validation

Before indexing begins, the script scans blobs in the `knowledge_base_lab/` prefix to identify oversized PDFs that exceed the ContentUnderstandingSkill's 300-page limit. Flagged documents are logged to `src/logs/skipped_documents.log`. Skip with `--skip-validation`.

### Step 1 — Create / Update Index (`hr_lab_index`)

Delegates to `AzureAISearchClient.create_index()` which provisions the index with:

| Field | Type | Searchable | Filterable | Retrievable | Notes |
|---|---|---|---|---|---|
| `id` | `Edm.String` | No | Yes | No | Primary key, keyword analyzer |
| `blob_url` | `Edm.String` | No | No | Yes | Blob storage URI |
| `snippet` | `Edm.String` | Yes | No | Yes | Chunk text content |
| `snippet_with_source` | `Edm.String` | Yes | No | Yes | Chunk + source path |
| `metadata_storage_name` | `Edm.String` | Yes | Yes | Yes | Original blob filename |
| `metadata_storage_path` | `Edm.String` | Yes | Yes | Yes | Full blob path |
| `parent_title` | `Edm.String` | Yes | Yes | Yes | Document title (no extension) |
| `policy_number` | `Edm.String` | Yes | Yes | Yes | Extracted policy number |
| `snippet_parent_id` | `Edm.String` | No | Yes | No | Links chunk → parent |
| `snippet_vector` | `Collection(Edm.Single)` | Vector | No | No | 1536-dim embedding |

**Vector search**: HNSW algorithm (cosine metric, m=4, efConstruction=400, efSearch=500) with int8 scalar quantization and 4× rescoring oversampling.

**Semantic search**: `hr-lab-semantic-config` using BoostedRerankerScore with `snippet` as the prioritized content field.

### Step 2 — Create Data Source

Creates a `SearchIndexerDataSourceConnection` named `hr-lab-index-datasource` pointing to the `ask-hr-knowledge` blob container. Authentication priority:

1. `AZURE_STORAGE_RESOURCE_ID` (managed identity via ARM ResourceId)
2. `AZURE_STORAGE_CONNECTION_STRING` (connection string)
3. `AZURE_STORAGE_ACCOUNT_URL` → resolves ARM ResourceId via Azure CLI

### Step 3 — Create Skillset (`hr-lab-skillset`)

The skillset uses **integrated vectorization** with ContentUnderstandingSkill for document cracking + chunking and AzureOpenAIEmbeddingSkill for vector generation.

#### Skill 1: ContentUnderstandingSkill

- **Context**: `/document`
- **Input**: `/document/file_data` (raw document bytes)
- **Output**: `/document/text_sections` (array of chunks)
- **Chunking**: 2000 characters max, 200 character overlap

#### Skill 2: AzureOpenAIEmbeddingSkill

- **Context**: `/document/text_sections/*`
- **Input**: `/document/text_sections/*/content`
- **Output**: `/document/text_sections/*/text_vector`
- **Model**: `text-embedding-3-small` (1536 dimensions)

#### Skill 3: MergeSkill (optional, disabled by default)

Prepends `metadata_storage_path` to each chunk's text to produce `snippet_with_source`. Disabled by default because `snippet_with_source` is built at query time from `blob_url` + `snippet` instead. Enable with `--merge-skill` or `USE_MERGE_SKILL=true`.

#### AI Services

The skillset attaches an `AIServicesAccountIdentity` using the Foundry AI Services endpoint (derived from `AZURE_OPENAI_ENDPOINT` or `AZURE_AI_SERVICES_URL`).

#### Index Projections

Chunks are projected into `hr_lab_index` with `includeIndexingParentDocuments` mode so both parent and chunk documents are indexed. The projection maps:

| Index Field | Enrichment Tree Source | Description |
|---|---|---|
| `snippet_vector` | `/document/text_sections/*/text_vector` | Embedding vector |
| `snippet` | `/document/text_sections/*/content` | Chunk text |
| `snippet_with_source` | `/document/text_sections/*/snippet_with_source` | Chunk + source |
| `blob_url` | `/document/metadata_storage_path` | Blob URI |
| `metadata_storage_name` | `/document/metadata_storage_name` | Original filename |
| `metadata_storage_path` | `/document/metadata_storage_path` | Full blob path |
| `parent_title` | `/document/parent_title` | Title without extension |
| `policy_number` | `/document/policy_number` | Numeric policy ID |

### Step 4 — Create and Run Indexer

Creates `hr-lab-index-indexer` connecting the data source → skillset → index. The indexer includes four **field mappings** that populate the enrichment tree before the skillset runs:

| Source (Blob) | Target (Enrichment Tree) | Mapping Function | Purpose |
|---|---|---|---|
| `metadata_storage_path` | `metadata_storage_path` | — | Pass-through |
| `metadata_storage_name` | `metadata_storage_name` | — | Pass-through |
| `metadata_storage_name` | `policy_number` | `extractTokenAtPosition(delimiter=" ", position=0)` | Extract policy number from filename |
| `metadata_storage_name` | `parent_title` | `extractTokenAtPosition(delimiter=".", position=0)` | Strip file extension for clean title |

**Example** — Blob filename: `51350 - Types of Leave_ Paid Time Off (PTO) (23472_2).docx`

| Enrichment Node | Value |
|---|---|
| `metadata_storage_name` | `51350 - Types of Leave_ Paid Time Off (PTO) (23472_2).docx` |
| `policy_number` | `51350` |
| `parent_title` | `51350 - Types of Leave_ Paid Time Off (PTO) (23472_2)` |

**Indexer configuration**:
- `dataToExtract`: `contentAndMetadata`
- `allowSkillsetToReadFileData`: `true`
- `excludedFileNameExtensions`: `.vsd,.vsdx,.mpp`
- `maxFailedItems`: 5 (configurable via `INDEXER_MAX_FAILED_ITEMS`)

### Step 5 — Provision Agentic Retrieval (optional)

Creates the knowledge source (`hr-knowledge-source`) and knowledge base (`hr-knowledge-base`) for Azure AI Search agentic retrieval. Requires `azure-search-documents>=11.7.0b2`.

- **Output mode**: `ANSWER_SYNTHESIS`
- **Retrieval reasoning effort**: `medium`
- **Source data fields**: `id`, `snippet`, `metadata_storage_name`, `metadata_storage_path`, `parent_title`, `policy_number`, `blob_url`

Skip with `--skip-agentic`.

## Configuration

All pipeline configuration is centralized in [`src/config/search_config.json`](../config/search_config.json). Key sections:

- `search_config` — index name, field names, top_k
- `vector_search` — HNSW algorithm, scalar quantization, vector profile
- `semantic_search` — semantic configuration, reranker, content fields
- `skillset` — skills, chunking, index projections, cognitive services, excluded extensions
- `agentic_retrieval` — knowledge source/base, output mode, retrieval instructions
- `blob_storage` — container name
- `foundry_agent` — model, retrieval/answer instructions

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `AZURE_SEARCH_ENDPOINT` | Yes | Azure AI Search service endpoint |
| `AZURE_OPENAI_ENDPOINT` | Yes | Azure OpenAI endpoint (base URL) |
| `AZURE_OPENAI_API_KEY` | No | OpenAI API key (omit for managed identity) |
| `AZURE_STORAGE_RESOURCE_ID` | One of three | ARM resource ID for managed identity |
| `AZURE_STORAGE_CONNECTION_STRING` | One of three | Blob storage connection string |
| `AZURE_STORAGE_ACCOUNT_URL` | One of three | Blob storage account URL |
| `AZURE_AI_SERVICES_URL` | No | AI Services URL (auto-derived from OpenAI endpoint) |
| `AZURE_SEARCH_INDEX_NAME` | No | Override index name (default: `hr_lab_index`) |
| `USE_MERGE_SKILL` | No | Enable MergeSkill (`true`/`1`/`yes`) |
| `INDEXER_MAX_FAILED_ITEMS` | No | Max tolerated indexer failures (default: 5) |
