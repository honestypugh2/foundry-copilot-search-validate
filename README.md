# Foundry Copilot Search Validate

End-to-end agentic retrieval pipeline using Azure AI Foundry Agents, Azure AI Search
(MCP), and Copilot Studio — with built-in citation validation and evaluation harness.
Documents are stored in Azure Blob Storage and indexed via Azure AI Search with
integrated vectorization. Uses a **Foundry Agent with MCP** (Model Context Protocol)
to retrieve, reason, and cite in a single pass.

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│  1. UPLOAD  (scripts/upload_to_blob.py)                            │
│     data/knowledge_base_lab/ ──► Azure Blob Storage                │
│                                    (ask-hr-knowledge container)    │
└─────────────────────────────────────┬───────────────────────────────┘
                                      │
┌─────────────────────────────────────▼───────────────────────────────┐
│  2. INDEX  (src/scripts/index_knowledge_base.py)               │
│     Azure Blob Storage                                             │
│       ↓  Indexer + Skillset                                        │
│     ContentUnderstanding → Embedding                               │
│       ↓  2000-char / 200-overlap chunks                            │
│     Azure AI Search Index (hr_lab_index)                           │
│                                                                    │
│     Also provisions:                                               │
│       Knowledge Source (hr-knowledge-source)                       │
│       Knowledge Base  (hr-knowledge-base)                          │
│       MCP endpoint   (knowledge_base_retrieve tool)                │
└─────────────────────────────────────┬───────────────────────────────┘
                                      │
┌─────────────────────────────────────▼───────────────────────────────┐
│  3. QUERY  (single-agent MCP — default)                            │
│     Foundry Agent (MCPTool, tool_choice="required")                │
│       → knowledge_base_retrieve (query planning, reranking)        │
│       → Reasoning + answer synthesis (GPT model)                   │
│       → Citations: 【message_idx:search_idx†source_name】           │
└─────────────────────────────────────┬───────────────────────────────┘
                                      │
                                      ▼
                          Copilot Studio / Teams
```

### Agentic Retrieval via MCP

The default pipeline uses a **single Foundry Agent** with an **MCPTool** that
connects to the Azure AI Search knowledge base MCP endpoint. This follows the
[Microsoft recommended pattern](https://learn.microsoft.com/en-us/azure/search/agentic-retrieval-how-to-create-pipeline)
for end-to-end agentic retrieval.

1. A **Knowledge Source** (`hr-knowledge-source`) references the `hr_lab_index`.
2. A **Knowledge Base** (`hr-knowledge-base`) wraps the knowledge source with
   `EXTRACTIVE_DATA` output mode and configurable retrieval instructions.
3. The knowledge base exposes an **MCP endpoint** with a `knowledge_base_retrieve` tool.
4. At query time the Foundry Agent calls the MCP tool (enforced by `tool_choice="required"`)
   which:
   - Analyses the conversation to infer the user's information need.
   - Decomposes compound queries into focused subqueries.
   - Runs the subqueries against the knowledge source.
   - Reranks results with the semantic ranker.
5. The agent receives retrieved content, reasons over it, and produces a
   natural-language answer with inline MCP citations.

Source trust is enforced at the **infrastructure level** — the knowledge base
only searches `hr_lab_index`, which only contains documents from the trusted
`ask-hr-knowledge` blob container.

## Pipeline Modes

The orchestrator (`FoundryAgentOrchestrator`) supports two modes, controlled by
the `PIPELINE_MODE` environment variable.

### Default: Single-Agent MCP (`PIPELINE_MODE=single_agent`)

```
User Query → Foundry Agent (MCPTool + tool_choice="required") → Answer with Citations
```

| Concern | Single-Agent |
|---------|-------------|
| Azure AI Search calls | 1 |
| Latency | Low |
| Citation quality | Agent-native MCP annotations |
| Source trust | Enforced at index level |

### Optional: Multi-Step Pipeline (`PIPELINE_MODE=multi_step`)

> **Not recommended for production.** Provided as a learning reference for
> multi-agent coordination patterns. Performs double retrieval.

| Step | Agent                       | Purpose                                                |
|------|-----------------------------|--------------------------------------------------------|
| 1    | **RetrievalAgent**          | Hybrid search (text + vector + semantic ranking)       |
| 2    | **SourceValidatorAgent**    | Verify provenance (ask-hr-knowledge container)         |
| 3    | **ReferenceValidatorAgent** | Extract citations + check grounding                    |
| 4    | **AnswerSynthesisAgent**    | Foundry Agent + MCP answer synthesis                   |

### Optional: Citation Validation (`VALIDATE_CITATIONS=true`)

Post-processes the agent response to verify MCP citation annotations
(`【message_idx:search_idx†source_name】`). Results returned under the
`citation_validation` key.

The orchestrator uses Python `async/await` with the `azure-ai-projects` SDK
(`AIProjectClient`) to coordinate the pipeline. Agents are registered as
PromptAgent definitions in the Foundry Portal via `register_agents.py`.

## Directory Structure

```
copilot/                                # Copilot Studio integration
├── manifest.json
└── openapi.yaml
src/
├── agents/                             # Foundry Agent Service (production)
│   ├── __init__.py
│   ├── foundry_client.py               # Shared AIProjectClient + OpenAI helpers
│   ├── register_agents.py              # One-time Foundry Agent registration
│   ├── retrieval_agent.py              # Agentic retrieval / hybrid search
│   ├── source_validator_agent.py       # Source trust validation
│   ├── reference_validator_agent.py    # Citation extraction + grounding
│   ├── answer_synthesis_agent.py       # Answer synthesis (MCP or context-based)
│   └── sequential_orchestrator_foundry.py  # Main orchestrator (single-agent MCP)
├── agents_af/                          # Agent Framework path (alternative)
│   ├── retrieval_agent.py
│   ├── source_validator_agent.py
│   ├── reference_validator_agent.py
│   ├── answer_synthesis_agent.py
│   └── sequential_orchestrator.py      # SequentialBuilder orchestrator
├── search/
│   ├── __init__.py
│   └── azure_ai_search_client.py
├── api/
│   ├── __init__.py
│   ├── function_app.py
│   └── models.py
├── config/
│   ├── search_config.json
│   └── .env.example
├── scripts/
│   ├── index_knowledge_base.py
│   ├── upload_to_blob.py
│   └── regenerate_encrypted_docx.py
├── tests/
│   ├── conftest.py
│   ├── test_mcp_query_retrieval.py
│   ├── test_full_flow.py
│   ├── test_agents_foundry.py
│   └── ...
├── docs/
│   ├── AgentArchitecturePaths.md
│   ├── FoundryAgentArchitecture.md
│   ├── FoundryAgentTesting.md
│   ├── IntegratedVectorizationPipeline.md
│   ├── CopilotStudioIntegration.md
│   └── MergeSkillRecommendations.md
├── logs/
├── requirements.txt
└── README.md
```

## Skillset Pipeline

The Azure AI Search skillset (`hr-lab-skillset`) uses **2 skills** by default
(MergeSkill is optional, disabled by default — see `docs/MergeSkillRecommendations.md`):

1. **ContentUnderstandingSkill** – extracts `text_sections` from documents
   (2000-char chunks with 200-char overlap, markdown-formatted)
2. **AzureOpenAIEmbeddingSkill** – generates `text-embedding-3-small`
   vectors (1536 dimensions) for each chunk

Index projections write `snippet_vector`, `snippet`, `snippet_with_source`,
and `blob_url` into the child index `hr_lab_index`.

`snippet_with_source` is constructed at **query time** from `blob_url` + `snippet`
(see `MergeSkillRecommendations.md` for rationale).

## Quick Start

```bash
# 1. Copy .env.example and fill in your values
cp config/.env.example .env

# 2. Install dependencies
uv sync

# 3. Upload knowledge base documents to Azure Blob Storage
uv run python -m scripts.upload_to_blob

# 4. Create the search index, skillset, indexer, and agentic retrieval resources
PYTHONPATH=$PWD/src uv run python src/scripts/index_knowledge_base.py

# 5. Register Foundry Agents (one-time)
PYTHONPATH=$PWD/src uv run python -m agents.register_agents

# 6. Run the function app locally
func start
```

### End-to-End Pipeline

| Step | Script | What It Does |
|------|--------|-----|
| **Upload** | `scripts/upload_to_blob.py` | Uploads documents from `data/knowledge_base_lab/` to the `ask-hr-knowledge` Blob container |
| **Index** | `src/scripts/index_knowledge_base.py` | Creates the search index, data source, skillset, and indexer that reads from Blob Storage; provisions agentic retrieval resources |
| **Query** | `src/api/function_app.py` | Serves the `/api/ask` endpoint — single-agent MCP pipeline by default |

> **Note**: The upload and indexing scripts are intentionally separate.
> `upload_to_blob.py` puts documents into Blob Storage.
> `index_knowledge_base.py` creates the Azure AI Search infrastructure
> (index, data source, skillset, indexer) that processes those documents,
> and provisions the Knowledge Source + Knowledge Base used for agentic
> retrieval at query time. Agentic retrieval is **not** used during
> indexing — it is a query-time feature.

## Copilot Studio Integration

The `copilot/manifest.json` defines the Copilot Studio extension pointing at
the Azure Function `/api/ask` endpoint. The `copilot/openapi.yaml` provides
the OpenAPI spec for the action. See `docs/CopilotStudioIntegration.md` for
the full setup guide.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `PIPELINE_MODE` | `single_agent` | `single_agent` (recommended) or `multi_step` (learning) |
| `VALIDATE_CITATIONS` | `false` | Enable post-processing citation validation |
| `AZURE_AI_PROJECT_ENDPOINT` | — | Foundry project endpoint |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | `gpt-5` | Model deployment for the agent |
| `AZURE_SEARCH_ENDPOINT` | — | Azure AI Search endpoint |
| `AZURE_SEARCH_INDEX` | `hr_lab_index` | Search index name |

## Further Documentation

| Document | Description |
|----------|-------------|
| `docs/FoundryAgentArchitecture.md` | MCP tool, pipeline modes, citation validation, agent instructions |
| `docs/AgentArchitecturePaths.md` | Comparison of Foundry Agent Service vs Agent Framework paths |
| `docs/FoundryAgentTesting.md` | How to test the pipeline locally and view results in Foundry Portal |
| `docs/IntegratedVectorizationPipeline.md` | Indexing pipeline: skillset, chunking, projections, agentic retrieval |
| `docs/CopilotStudioIntegration.md` | End-to-end Copilot Studio setup (Knowledge Source + Function Tool) |
| `docs/MergeSkillRecommendations.md` | Why MergeSkill is disabled and query-time construction is preferred |
