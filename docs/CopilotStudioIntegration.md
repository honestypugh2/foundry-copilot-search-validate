# Copilot Studio Integration Guide (Lab Pipeline)

> ⚠️ **Development reference only.** Production deployments must follow the
> [Azure Well-Architected Framework](https://learn.microsoft.com/azure/well-architected/)
> and Microsoft best practices. The supported pattern for Copilot Studio
> calling Foundry Agents with MCP for agentic retrieval uses **GA-only**
> Python packages.

This document describes how **Copilot Studio** integrates with the HR Policy
Knowledge Lab pipeline — specifically the Azure AI Search index and the
Azure AI Foundry Agent — so employees can ask HR questions from Teams or web chat.

## Architecture

```
Employee (Teams / Web Chat)
        │
        ▼
Copilot Studio Bot
        │
        ├──► Azure AI Search (hr_lab_index)
        │        Knowledge Source → Knowledge Base (Agentic Retrieval)
        │        Query planning → subquery decomposition →
        │          semantic ranking → answer synthesis
        │        Index populated by indexer + skillset pipeline:
        │          ContentUnderstanding → MergeSkill → Embedding
        │        Data source: Azure Blob Storage (ask-hr-knowledge)
        │
        └──► /api/ask endpoint (Azure Function)
                 Sequential pipeline (agentic retrieval by default):
                   Retrieval (agentic) → Source Validation →
                   Reference Validation → Answer Synthesis
        │
        ▼
Grounded HR Policy Answer (with citations)
```

> **Default retrieval mode**: The pipeline defaults to **agentic retrieval**
> via the `KnowledgeBaseRetrievalClient`. When the agentic retrieval SDK
> classes are available and `knowledge_base_name` is configured in
> `search_config.json`, the `RetrievalAgent` uses the Knowledge Base for
> query planning, subquery decomposition, semantic ranking, and answer
> synthesis. It falls back to deterministic hybrid search only when
> agentic retrieval is unavailable or fails.

## Two Integration Paths

| Path | How It Works | When to Use |
|------|-------------|-------------|
| **Knowledge Source (direct)** | Copilot Studio queries `hr_lab_index` directly via its native Azure AI Search connector. Supports **text search + vector search** (integrated vectorization) **+ semantic ranker**. Copilot Studio's built-in LLM synthesizes the answer. | Simple Q&A, broad policy lookups |
| **Foundry Agent Action (default)** | Copilot Studio calls the `/api/ask` Azure Function, which runs the 4-step sequential pipeline with **agentic retrieval** as the default retrieval mode (Knowledge Base → query planning → subquery decomposition → semantic ranking → answer synthesis), plus source validation, reference grounding, and GPT-4.1 answer synthesis. Falls back to hybrid search if agentic retrieval is unavailable. | Complex questions, grounded citations, multi-step reasoning |

---

## Prerequisites

| Requirement | Details |
|---|---|
| Copilot Studio license | Power Virtual Agents / Copilot Studio |
| Power Platform environment | Your tenant environment |
| Azure AI Search index | `hr_lab_index` (deployed via this project) |
| Azure AI Search API key | Reader access (query key) |
| Azure Function App | Deployed `/api/ask` endpoint |
| Azure AI Foundry project | For the answer synthesis agent |

---

## Path 1: Azure AI Search as Knowledge Source

### Step 1: Create a Copilot

1. Go to [Copilot Studio](https://copilotstudio.microsoft.com)
2. Click **Create** → **New copilot**
3. Name: `Ask HR Policy Agent (Lab)`
4. Description: `Answers employee HR policy questions using the ASK HR knowledge base`
5. Language: English

### Step 2: Add Azure AI Search Knowledge Source

1. In the copilot editor, go to **Knowledge** → **Add knowledge**
2. Select **Azure AI Search** under Featured
3. Click **Create new connection**
4. Authentication: **Access Key**
5. Enter connection details:

| Field | Value |
|---|---|
| Endpoint URL | `https://<your-search-service>.search.windows.net` |
| API Key | Your query key |

6. Click **Create** → green check confirms connection
7. Click **Next** → enter index name: `hr_lab_index`
8. Click **Add to agent** → wait for status **Ready**

> **Integrated Vectorization**: Copilot Studio supports
> [vectorized indexes using integrated vectorization](https://learn.microsoft.com/en-us/azure/search/vector-search-integrated-vectorization).
> Because `hr_lab_index` uses integrated vectorization (the same embedding
> model used at index time vectorizes the incoming prompt at runtime),
> Copilot Studio can execute vector queries against the index automatically.

> **Semantic Ranker**: The index has semantic configuration
> `hr-lab-semantic-config` with the `snippet` field prioritized for content
> ranking. Copilot Studio automatically uses the semantic ranker when
> querying an index with a semantic configuration.

> **Note**: Copilot Studio's native connector queries the search index
> directly — it does **not** use the Knowledge Base or agentic retrieval
> pipeline. For agentic retrieval with query planning, subquery
> decomposition, and LLM-driven answer synthesis, use Path 2.

### Step 3: Configure Agent Instructions

1. On the **Overview** page, find the **Instructions** text box
2. Enter:

```
You are an HR policy assistant. Answer questions ONLY using the HR policy
documents retrieved from the knowledge base.

- Always cite the specific policy number (e.g., Policy 51350 – Paid Time Off)
- Include the exact filename and source path when available
- If a policy doesn't cover the question, say so clearly
- Never provide legal advice
- Use professional, clear language
- Reference the exact policy title and section when possible

Available HR policies include:
- Leave policies (PTO, Short-Term Disability, Holiday Pay)
- Hiring policies (Pre-employment, Rehiring, Probationary Period)
- Career paths (HR Generalist, Data Management)
- Dress code (Uniform and Non-Uniform)
- IT policies (Acceptable Use, Security, AI/LLM, Mobile Device)
- Safety (Blood Borne Pathogens)
- Ethics (Code of Ethics)
```

### Step 4: Configure Generative AI Settings

1. Go to **Settings** → **Generative AI**
2. Confirm **Use generative AI orchestration** is **Yes**
3. Turn **off** "Allow the AI to use its own general knowledge" (recommended for grounded-only answers)
4. Set **Content moderation** to **High**
5. Click **Save**

---

## Path 2: Foundry Agent via Azure Function Tool

This path gives Copilot Studio access to the full sequential pipeline
including **agentic retrieval** (same Knowledge Base as Path 1), plus
source validation, reference grounding, and GPT-4.1 answer synthesis.

> **UI Note**: Copilot Studio renamed **Actions** to **Tools** (April 2025+).
> The steps below reflect the current UI.

> **Prerequisites from Path 1**: Before adding the tool, complete Path 1
> **Step 3** (Configure Agent Instructions) and **Step 4** (Configure
> Generative AI Settings). Instructions tell the agent how to format
> responses and cite policy numbers; Generative AI settings enable
> orchestration and disable general knowledge. Path 1 **Step 2** (Add
> Azure AI Search Knowledge Source) is **optional** for Path 2 — the
> `/api/ask` tool runs its own retrieval pipeline against `hr_lab_index`.

### Step 1: Add REST API Tool

1. Open your agent → go to the **Tools** tab
2. Click **Add a tool** → **New tool** → **REST API**
3. Upload the OpenAPI spec: `copilot/openapi.yaml`
   - Copilot Studio accepts OpenAPI v3 and auto-converts to v2
4. Verify the spec details show the `askHRPolicy` operation → click **Next**
5. Improve the **Description**: `Searches HR policy knowledge base using agentic retrieval, validates sources, and returns grounded answers with citations from the ASK HR knowledge base`
6. Select a **Solution** (or leave blank for auto-created) → click **Next**

### Step 2: Configure Authentication

1. Select **API key** (the Azure Function uses `auth_level=FUNCTION`)
2. Configure:

| Field | Value |
|---|---|
| Parameter label | `Function Key` |
| Parameter name | `code` |
| Parameter location | `Query` |

3. Click **Next**

### Step 3: Select and Configure the Tool

1. Select the **askHRPolicy** tool from the list
2. Configure:

| Field | Value |
|---|---|
| Tool name | `Ask HR Policy` |
| Tool description | `Ask an HR policy question. Runs agentic retrieval pipeline with source validation, reference grounding, and citation-backed answers.` |

3. Review input/output parameters:
   - **Input**: `query` (string) — the user's HR question
   - **Outputs**: `status`, `matches`, `references`, `is_grounded`, `answer`
4. Fill in any blank parameter descriptions → click **Next**

### Step 4: Review and Publish

1. Review the tool configuration → click **Next**
2. Wait for the tool to publish
3. Click **Create connection** → enter your Azure Function key
4. Select **Add and configure** to add the tool to your agent

### Step 5: Configure Tool Behavior

On the tool configuration page:

1. Under **Details** → ensure **Allow agent to decide dynamically when to use the tool** is checked
2. Under **Completion** → select **Write the response with generative AI** (lets Copilot Studio format the answer with citations)
3. Click **Save**

### Step 6: Wire the Tool in Topics (Optional)

If you prefer explicit routing instead of generative orchestration:

1. Go to **Topics** → create or edit a topic
2. Add node (+) → **Add a tool** → select `Ask HR Policy`
3. Map the user's message to the `query` input
4. Under **Completion**, author a specific response template referencing output variables

### Alternative: Add Foundry Agent Directly

Copilot Studio can also delegate to the Azure AI Foundry Agent:

1. Go to **Tools** → **Add a tool** → **New tool** → select **Azure AI Foundry agent**
2. Select your AI Foundry project and the HR Policy Answer Agent
3. The Foundry agent runs as a sub-agent for complex tasks

See: [Add a Foundry agent to Copilot Studio](https://learn.microsoft.com/en-us/microsoft-copilot-studio/add-agent-foundry-agent)

---

## How Copilot Studio Uses Agentic Retrieval

When an employee asks a question in Teams or web chat:

1. **Copilot Studio receives the message** via the Teams/web channel
2. **Generative orchestration** evaluates the user's intent and selects the appropriate knowledge source or action
3. **If using Path 1** (direct search): Copilot Studio queries `hr_lab_index` directly with text + vector (integrated vectorization) + semantic ranker, then synthesizes a response using its built-in LLM
4. **If using Path 2** (Foundry agent action): Copilot Studio calls `POST /api/ask` with the user's query, which triggers:
   - **Step 1 – Retrieval Agent**: Agentic retrieval via Knowledge Base (query planning → subquery decomposition → semantic ranking → answer synthesis) against `hr_lab_index`, falls back to hybrid search
   - **Step 2 – Source Validator**: Filters to trusted `ask-hr-knowledge` container
   - **Step 3 – Reference Validator**: Extracts citations with filenames, policy numbers, paths
   - **Step 4 – Answer Synthesis**: Uses agentic retrieval answer when available; falls back to Foundry Agent (GPT-4.1) with configured `answerSynthesis` output mode
5. **Copilot Studio displays the response** with the synthesized answer and citation references
6. **Follow-up questions** continue the conversation with context

### Key Difference: Direct Search vs. Foundry Agent Pipeline

| Capability | Direct Search (Path 1) | Foundry Agent Pipeline (Path 2) |
|---|---|---|
| Search type | Text + vector (integrated vectorization) + semantic ranker | Agentic retrieval (query planning + subqueries + semantic ranking + answer synthesis) with hybrid fallback |
| Source validation | None | Provenance check (trusted container) |
| Reference grounding | URL-based citations (`metadata_storage_path`) | Structured citations with policy numbers |
| Answer synthesis | Copilot Studio built-in LLM | Agentic retrieval answer (pass-through) or Foundry Agent GPT-4.1 (fallback) |
| Query planning | None — single query | LLM-driven subquery decomposition |
| Retrieval instructions | Not configurable | Configurable via `search_config.json` |
| Additional pipeline steps | None | Source validation → reference validation → answer synthesis |

---

## Searchable Fields

The `hr_lab_index` includes these searchable fields ensuring filenames and
folder paths are discoverable:

| Field | Type | Purpose |
|---|---|---|
| `snippet` | SearchableField | Chunk text content |
| `snippet_with_source` | SearchableField | Chunk content + blob URL |
| `metadata_storage_name` | SearchableField (filterable) | Original filename |
| `metadata_storage_path` | SearchableField (filterable) | Full blob storage path |
| `parent_title` | SearchableField (filterable) | Parent document title |
| `policy_number` | SearchableField (filterable) | Policy number (e.g., 51350) |

---

## Publish to Teams

1. Go to **Channels** → **Microsoft Teams**
2. Click **Turn on Teams**
3. Configure:
   - Display name: `Ask HR (Lab)`
   - Description: `Ask questions about HR policies`
4. Click **Publish**
5. Share the bot link with employees

---

## Testing

### Test in Copilot Studio
1. Use the **Test** pane
2. Try these questions:
   - "What is the PTO policy?"
   - "How many holidays do we get?"
   - "What's the dress code?"
   - "Tell me about the probationary period"
   - "What is the AI/LLM policy?"
   - "What does the IT acceptable use policy say?"

### Verify Grounding
- Check that answers include policy numbers (e.g., Policy 51350)
- Verify citations reference actual filenames from the knowledge base
- Confirm the bot says "I don't have information about that" for off-topic questions

---

## Troubleshooting

| Issue | Solution |
|---|---|
| No results returned | Verify `hr_lab_index` has documents and is queryable |
| Wrong policies cited | Check field mappings and parent metadata projections |
| Generic answers | Ensure "general knowledge" is turned off in Generative AI settings |
| Connection failed | Verify AI Search endpoint and API key |
| Foundry agent timeout | Check Azure Function health and Foundry project endpoint |

---

## References

- [Azure AI Search knowledge in Copilot Studio](https://learn.microsoft.com/en-us/microsoft-copilot-studio/knowledge-azure-ai-search)
- [Add a Foundry agent to Copilot Studio](https://learn.microsoft.com/en-us/microsoft-copilot-studio/add-agent-foundry-agent)
- [Azure-Samples/Copilot-Studio-with-Azure-AI-Search](https://github.com/Azure-Samples/Copilot-Studio-with-Azure-AI-Search)

---

## End-to-End: Upload, Index, Query

Getting the knowledge base ready for queries requires two scripts run in order,
followed by the query-time pipeline.

### Step 1 — Upload Documents to Blob Storage

The `scripts/upload_to_blob.py` script uploads files from
`data/knowledge_base_lab/` to the `ask-hr-knowledge` Azure Blob container.

```bash
# Upload all knowledge base documents
uv run python -m scripts.upload_to_blob

# Preview what would be uploaded
uv run python -m scripts.upload_to_blob --dry-run
```

> This script lives in the repo-root `scripts/` folder (not `src/scripts/`).

### Step 2 — Create Search Infrastructure and Index

The `src/scripts/index_knowledge_base.py` script creates the Azure AI Search
resources that read from Blob Storage and index the documents. It does **not**
upload documents — that is handled by Step 1.

```bash
# Full setup: index + data source + skillset + indexer + agentic retrieval
PYTHONPATH=$PWD/src uv run python src/scripts/index_knowledge_base.py

# Skip agentic retrieval provisioning
PYTHONPATH=$PWD/src uv run python src/scripts/index_knowledge_base.py --skip-agentic

# Reset: delete and recreate the indexer
PYTHONPATH=$PWD/src uv run python src/scripts/index_knowledge_base.py --reset

# Dry run: validate config without creating resources
PYTHONPATH=$PWD/src uv run python src/scripts/index_knowledge_base.py --dry-run
```

The script creates:
1. **Search index** (`hr_lab_index`) with HNSW vector search + scalar quantization + semantic config
2. **Data source** pointing to the `ask-hr-knowledge` Blob Storage container
3. **Skillset** (`hr-lab-skillset`) with ContentUnderstanding → MergeSkill → Embedding
4. **Indexer** that runs the skillset pipeline on blob documents
5. **Knowledge source** + **Knowledge base** for agentic retrieval (unless `--skip-agentic`)

### Step 3 — Query (Agentic Retrieval at Runtime)

Agentic retrieval is a **query-time** feature — it is not used during indexing.
Once the Knowledge Source and Knowledge Base are provisioned (Step 2), the
`RetrievalAgent` uses `KnowledgeBaseRetrievalClient` to send conversation
messages to the Knowledge Base, which handles query planning, subquery
decomposition, semantic ranking, and answer synthesis.

| Concern | Script / Component | Notes |
|---|---|---|
| Upload docs to Blob | `scripts/upload_to_blob.py` | Preserves directory structure under `data/` |
| Create index + indexer | `src/scripts/index_knowledge_base.py` | Standard indexer + skillset pipeline |
| Provision agentic retrieval | Same script (Step 5) | Knowledge Source + Knowledge Base |
| Query with agentic retrieval | `RetrievalAgent` → `AzureAISearchClient.agentic_retrieve()` | Query-time only |
