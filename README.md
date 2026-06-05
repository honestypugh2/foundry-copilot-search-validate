# Foundry Copilot Search Validate

> ⚠️ **Development reference only.** This repository is a learning / evaluation
> harness. Production deployments **must** follow the
> [Azure Well-Architected Framework](https://learn.microsoft.com/azure/well-architected/)
> and Microsoft best practices for security, reliability, cost, operations, and
> performance. Use the GA-only pattern below; do not ship preview packages.
>
> **Default supported pattern:** Copilot Studio → Azure AI Foundry Agent +
> Azure AI Search MCP for agentic retrieval, using **GA-only** Python
> dependencies (`azure-ai-projects` 2.2.0 GA, `azure-search-documents` 12.0.0
> GA). Preview Microsoft Agent Framework experimentation lives under
> [`src/agents_af/`](src/agents_af/README.md) and must be installed in a
> separate virtual environment — see that folder's README. **Never** mix the
> preview path into a production deployment.

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

The orchestrator is selected via the `ORCHESTRATOR_PATTERN` environment variable
(default: `A`). Both patterns use the `get_orchestrator()` factory:

```python
from agents.orchestrator_factory import get_orchestrator
orchestrator = get_orchestrator()
result = orchestrator.process_query("What is the PTO policy?")
```

### Pattern A: Single-Agent MCP (`ORCHESTRATOR_PATTERN=A`, default)

```
User Query → Foundry Agent (MCPTool + tool_choice="required") → Answer with Citations
```

A single Foundry Agent with an MCPTool handles retrieval, reasoning, and citation
formatting in one pass. The platform handles the MCP call transparently.

| Concern | Single-Agent |
|---------|-------------|
| Azure AI Search calls | 1 |
| Latency | Low |
| Citation quality | Agent-native MCP annotations |
| Source trust | Enforced at index level |

### Pattern B: Hybrid MCP + Metadata Lookup (`ORCHESTRATOR_PATTERN=B`)

```
User Query → Client-Side Query Classification
    ├─ Content question → HRPolicyAgentB (MCPTool only) → Answer with Citations
    └─ File-location question → file_metadata_lookup (direct index search)
                                  → HRPolicyAgentB-FileLocation (no tools, formatting only)
```

Pattern B uses **client-side query classification** to route queries to the optimal path:

- **Content questions** → `HRPolicyAgentB` (Foundry Agent + MCPTool, platform handles
  MCP natively — same retrieval semantics as Pattern A, full KB citations).
- **File-location questions** → `file_metadata_lookup` called directly (bypasses MCP),
  results formatted by `HRPolicyAgentB-FileLocation` (a no-tools agent). Returns
  deterministic metadata: `metadata_storage_path`, `metadata_storage_name`, `blob_url`.

Two stable agent names instead of one prevent definition churn — see
[`src/agents/foundry_client.py`](src/agents/foundry_client.py) `_ensure_foundry_agent`
and `RECREATE_FOUNDRY_AGENTS` in the env-var table below.

This provides **deterministic file paths** — no LLM hallucination risk for metadata —
while preserving full KB retrieval with native MCP citations for content questions.

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
├── observability.py                    # enable_tracing() — Azure Monitor + OTel bootstrap
├── function_app.py                     # Azure Functions entry-points (/api/ask)
├── models.py                           # Pydantic request / response models
├── host.json
├── local.settings.json
├── requirements.txt
├── agents/                             # Foundry Agent Service (production)
│   ├── __init__.py
│   ├── foundry_client.py               # Shared AIProjectClient + _ensure_foundry_agent helper
│   ├── orchestrator_factory.py         # Pattern A/B routing (get_orchestrator)
│   ├── orchestrator_pattern_b.py       # Pattern B: MCP + metadata lookup
│   ├── register_agents.py              # One-time Foundry Agent registration
│   ├── retrieval_agent.py              # Agentic retrieval / hybrid search
│   ├── source_validator_agent.py       # Source trust validation
│   ├── reference_validator_agent.py    # Citation extraction + grounding
│   ├── answer_synthesis_agent.py       # Answer synthesis (MCP or context-based)
│   └── sequential_orchestrator_foundry.py  # Pattern A orchestrator (single-agent MCP)
├── agents_af/                          # Agent Framework path (alternative, preview deps)
│   ├── retrieval_agent.py
│   ├── source_validator_agent.py
│   ├── reference_validator_agent.py
│   ├── answer_synthesis_agent.py
│   └── sequential_orchestrator.py      # SequentialBuilder orchestrator
├── search/
│   ├── __init__.py
│   └── azure_ai_search_client.py
└── config/
    └── search_config.json
scripts/
├── index_knowledge_base.py
├── upload_to_blob.py
└── regenerate_encrypted_docx.py
tests/
├── conftest.py
├── test_mcp_query_retrieval.py
├── test_full_flow.py
├── test_agents_foundry.py
├── test_retrieval_patterns_live.py     # Multi-pattern live driver
└── ...
docs/
├── AgentArchitecturePaths.md
├── FoundryAgentArchitecture.md
├── FoundryAgentTesting.md
├── IntegratedVectorizationPipeline.md
├── CopilotStudioIntegration.md
└── MergeSkillRecommendations.md
infra/
├── main.bicep                          # Subscription-scope entry point
└── bicep/
    ├── main.bicep                      # Resource definitions (incl. AppInsights project connection)
    └── connect-appinsights.bicep       # Targeted: add only project ↔ AppInsights connection
logs/
requirements.txt                        # GA-only runtime deps
requirements-agents-af.txt              # Preview Agent Framework deps (separate venv)
README.md
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
cp .env.example .env

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
| `ORCHESTRATOR_PATTERN` | `A` | `A` (single-agent MCP) or `B` (hybrid MCP + metadata lookup) |
| `PIPELINE_MODE` | `single_agent` | `single_agent` (recommended) or `multi_step` (learning) |
| `VALIDATE_CITATIONS` | `false` | Enable post-processing citation validation |
| `AZURE_AI_PROJECT_ENDPOINT` | — | Foundry project endpoint |
| `AZURE_AI_MODEL_DEPLOYMENT_NAME` | `gpt-5` | Model deployment for the agent |
| `AZURE_SEARCH_ENDPOINT` | — | Azure AI Search endpoint |
| `AZURE_SEARCH_INDEX` | `hr_lab_index` | Search index name |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | — | App Insights connection string. When set, `src/observability.py` calls `configure_azure_monitor` so spans flow to App Insights and surface in the Foundry portal **Tracing** tab. Unset = tracing is a silent no-op. |
| `AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING` | `true` | Set by `enable_tracing()`. **Required** for the Azure AI Projects SDK to emit agent-run / tool-call spans. Without it the Tracing tab stays empty even when App Insights is wired up. |
| `AZURE_TRACING_GEN_AI_CONTENT_RECORDING_ENABLED` | `true` | Set by `enable_tracing()`. Captures prompt/completion text on spans. Set to `false` to scrub message contents. |
| `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` | `true` | Set by `enable_tracing()`. Same as above for OTel-native instrumentations. |
| `RECREATE_FOUNDRY_AGENTS` | `false` | Force `_ensure_foundry_agent` to mint a new agent version on every invocation. Default behavior is get-or-create — the orchestrator reuses the existing version of `HRPolicyAgent` / `HRPolicyAgentB` / `HRPolicyAgentB-FileLocation` so the portal stops prompting "Save the Agent" between runs. Flip to `true` only when intentionally editing instructions or tool definitions. |
| `PERSIST_FOUNDRY_AGENTS` | `true` | When `false`, the orchestrator deletes the agent version it touched at the end of each run. Leave at `true` to keep agents visible in the portal. |

## Observability / Foundry Portal Tracing

The orchestrators in `src/agents/` and `src/agents_af/` call
`enable_tracing()` (from [`src/observability.py`](src/observability.py)) at module
load. This:

1. Reads `APPLICATIONINSIGHTS_CONNECTION_STRING` from the env (no-op if unset).
2. Defaults `AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING=true` so the Azure AI
   Projects SDK actually emits agent / tool spans.
3. Calls `azure.monitor.opentelemetry.configure_azure_monitor(...)` to wire the
   OTel exporter to App Insights.
4. Best-effort calls `agent_framework.observability.setup_observability()` for
   the AF pipeline.
5. Registers an `atexit` hook that force-flushes the tracer provider so
   short-lived CLI / test runs don't drop buffered spans.

For the **Foundry portal Tracing tab** to populate you also need an
`AppInsights`-category connection on the project itself
(`Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01`). The
default lab Bicep already creates one in
[`infra/bicep/main.bicep`](infra/bicep/main.bicep). For an existing project
that's missing the connection, deploy the targeted module:

```bash
az deployment group create \
  -g <rg> \
  -f infra/bicep/connect-appinsights.bicep \
  -p foundryAccountName=<accountName> \
     foundryProjectName=<projectName> \
     appInsightsName=<existingAppInsightsName>
```

Install the runtime dependency with **prereleases enabled** (the OTel
instrumentation libraries it pulls are still in beta):

```bash
uv pip install --prerelease=allow 'azure-monitor-opentelemetry>=1.6.0,<2'
```

## Further Documentation

| Document | Description |
|----------|-------------|
| `docs/FoundryAgentArchitecture.md` | MCP tool, pipeline modes, citation validation, agent instructions |
| `docs/AgentArchitecturePaths.md` | Comparison of Foundry Agent Service vs Agent Framework paths |
| `docs/FoundryAgentTesting.md` | How to test the pipeline locally and view results in Foundry Portal |
| `docs/IntegratedVectorizationPipeline.md` | Indexing pipeline: skillset, chunking, projections, agentic retrieval |
| `docs/CopilotStudioIntegration.md` | End-to-end Copilot Studio setup (Knowledge Source + Function Tool) |
| `docs/MergeSkillRecommendations.md` | Why MergeSkill is disabled and query-time construction is preferred |
