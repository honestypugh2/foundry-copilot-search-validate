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
                 FoundryAgentOrchestrator:
                   Pattern A (ORCHESTRATOR_PATTERN=A, default):
                     Single-Agent MCP → knowledge_base_retrieve →
                     answer with citations
                   Pattern B (ORCHESTRATOR_PATTERN=B):
                     Hybrid MCP + metadata lookup (classified routing)
                   Optional (PIPELINE_MODE=multi_step):
                     4-step pipeline: Retrieval → Source Validation →
                     Reference Validation → Answer Synthesis
        │
        ▼
Grounded HR Policy Answer (with citations)
```

> **Default orchestrator mode**: The `/api/ask` pipeline defaults to
> **Pattern A — Single-Agent MCP** (`ORCHESTRATOR_PATTERN=A`). A single
> Foundry Agent with an `MCPTool` calls `knowledge_base_retrieve` against the
> Knowledge Base, which handles query planning, subquery decomposition,
> semantic ranking, and answer synthesis, then returns a citation-backed
> answer in one pass. Set `ORCHESTRATOR_PATTERN=B` to switch to **Pattern B —
> Hybrid MCP + Metadata Lookup**, which routes file-location queries to a
> deterministic metadata lookup. The 4-step sequential pipeline (retrieval →
> source validation → reference validation → answer synthesis) is **optional**
> and enabled only via `PIPELINE_MODE=multi_step`.

## Two Integration Paths

| Path | How It Works | When to Use |
|------|-------------|-------------|
| **Knowledge Source (direct)** | Copilot Studio queries `hr_lab_index` directly via its native Azure AI Search connector. Supports **text search + vector search** (integrated vectorization) **+ semantic ranker**. Copilot Studio's built-in LLM synthesizes the answer. | Simple Q&A, broad policy lookups |
| **Foundry Agent Action (default)** | Copilot Studio calls the `/api/ask` Azure Function, which runs the `FoundryAgentOrchestrator`. By default it uses **Pattern A — Single-Agent MCP** (`ORCHESTRATOR_PATTERN=A`): a single Foundry Agent with `MCPTool` calls `knowledge_base_retrieve` (query planning → subquery decomposition → semantic ranking → answer synthesis) and returns citation-backed answers. Set `ORCHESTRATOR_PATTERN=B` for **Pattern B — Hybrid MCP + Metadata Lookup** (classified routing for file-location queries). The 4-step sequential pipeline is **optional** (`PIPELINE_MODE=multi_step`). | Complex questions, grounded citations, multi-step reasoning |

---

## Orchestration Model — Copilot Studio Decides When to Call Foundry (Hybrid)

This model sits **on top of** Paths 1 and 2 — it is **not** a third backend
orchestrator pattern. The `ORCHESTRATOR_PATTERN` env var (A / B) still controls
what happens *inside* the Azure Function. This section controls a different
decision, one layer up: **Copilot Studio's generative orchestration decides,
per turn, whether to answer directly, answer from a small uploaded glossary, or
call the Foundry `askHRPolicy` tool** (which then runs Pattern A or B).

> **Why this is not a duplicate of Pattern B.** Pattern B's classifier
> (content vs. file-location) runs **server-side**, inside `/api/ask`, and
> always executes once the Function is called. The routing here runs
> **client-side**, inside Copilot Studio, and decides *whether to call
> `/api/ask` at all*. Keep `/api/ask` as the single retrieval brain — do **not**
> re-implement the content/file-location split in Copilot Studio instructions,
> or two classifiers will compete. The Copilot layer only chooses
> **answer-directly / answer-from-glossary / call-the-tool**.

### Four response paths

| Path | Handled by | Example |
|---|---|---|
| **Copilot only** | Copilot Studio | "Hi, I have an HR question" → greet + ask what policy area |
| **Copilot knowledge** | Copilot Studio + uploaded glossary | "What does PTO stand for?" → "Paid Time Off" (no Foundry call) |
| **Foundry only** | `askHRPolicy` tool | "What is the PTO policy?" → runs Pattern A/B, grounded answer |
| **Hybrid (both)** | Copilot + Foundry | "Summarize the PTO policy and tell me where it's stored" → `askHRPolicy`, then format with the glossary's policy-number map |

You steer the decision with **two levers**: agent **Instructions** (global
routing rules) and the **tool description** (the strongest per-turn signal).

### Lever 1 — Routing instructions (paste into agent Instructions)

This expands Path 1 **Step 3** with explicit *handle directly* vs. *call the
tool* rules so generative orchestration routes correctly. Paste into
**Overview → Instructions**:

```
You are the Ask HR Policy assistant. Copilot Studio orchestrates; the Foundry
Agent (via the askHRPolicy tool) does the policy retrieval. You never invent
HR policy content — you only present and cite answers returned by askHRPolicy.

GATHER CONTEXT FIRST:
If the user is vague ("I have an HR question", "help me with leave"), DO NOT
call a tool. Ask which policy area (PTO/leave, holiday pay, hiring, probation,
dress code, IT/AI use, safety, ethics) and any specifics.

WHEN TO HANDLE DIRECTLY (NO FOUNDRY CALL):
- Greetings and intake: "Hi", "I need help" -> greet and ask the policy area.
- Glossary / acronym lookups: "What does PTO / STD / BBP stand for?" ->
  answer from the uploaded quick reference guide.
- Policy-number lookups: "What's the policy number for Holiday Pay?" ->
  answer 50715 from the uploaded quick reference guide.

WHEN TO CALL askHRPolicy (Foundry):
- The user asks WHAT a policy says, HOW it works, eligibility, amounts,
  deadlines, or process ("What is the PTO policy?", "How much holiday pay?",
  "What's the probationary period?").
- The user asks WHERE a policy document is stored or its filename/path
  ("Where is the PTO policy stored?") — this returns a deterministic blob path
  when the backend runs Pattern B.

HYBRID:
- For "summarize X and tell me where it's stored", call askHRPolicy once and
  surface both the grounded answer and the source/citation it returns.

OFF-TOPIC:
- If the question is not about HR policy ("What's the capital of France?"),
  say you can only help with HR policy questions from the knowledge base.
  Do NOT call the tool and do NOT use general knowledge.

RESPONSE STYLE:
- Return the tool's `answer` text verbatim, preserving its citation markers
  (【message_idx:search_idx†source_name】).
- Always include the policy number when present (e.g., Policy 51350 - PTO).
- Be professional and concise; ask clarifying questions for vague requests.
```

### Lever 2 — Tool description (the strongest routing signal)

Generative orchestration weighs each tool's **description** heavily when
deciding whether to call it. Write it as *"use this tool when… do NOT use this
for…"*. This is the `description` of the `askHRPolicy` operation in
[copilot/openapi-v2.json](../copilot/openapi-v2.json) — tighten it to:

```
Use this tool when the user asks WHAT an HR policy says, HOW it works,
eligibility, amounts, deadlines, or process (PTO, leave, holiday pay, hiring,
probation, dress code, IT/AI acceptable use, safety, code of ethics), or WHERE
a policy document is stored (filename/blob path). It runs the Foundry Agent
(Pattern A single-agent MCP by default; Pattern B adds a deterministic
file-location lookup) and returns a grounded, citation-backed answer. Do NOT
use this tool for greetings, acronym/policy-number glossary lookups (answer
those from uploaded knowledge), or non-HR questions.
```

> If the tool is **over-called** (fires on greetings), tighten the *"do NOT
> use"* clause and add more glossary entries. If it is **under-called**
> (answers from general knowledge instead), add synonyms to the *"use when"*
> clause and confirm general knowledge is OFF (Path 1 Step 4).

### Question routing summary

| User says | Routed to | Source |
|---|---|---|
| "Hi, I have an HR question" | Copilot Studio | None (greeting) |
| "What does PTO stand for?" | Copilot Studio | Uploaded glossary |
| "What's the Holiday Pay policy number?" | Copilot Studio | Uploaded glossary |
| "What is the PTO policy?" | Foundry | `askHRPolicy` (Pattern A/B) |
| "Where is the PTO policy stored?" | Foundry | `askHRPolicy` (Pattern B path) |
| "Summarize PTO and where it's stored" | Both | Glossary + `askHRPolicy` |
| "What is the capital of France?" | Copilot Studio | Declines (off-topic) |

### Verify routing (test these in the Test pane)

| # | Prompt | Expected behavior |
|---|---|---|
| 1 | "Hi, I need HR help" | Copilot only — greets/clarifies, **no** Foundry call |
| 2 | "What does PTO stand for?" | Copilot knowledge — answers from glossary, **no** Foundry call |
| 3 | "What's the policy number for Holiday Pay?" | Copilot knowledge — answers `50715`, **no** Foundry call |
| 4 | "What is the PTO policy?" | Foundry — calls `askHRPolicy`, grounded answer cites Policy 51350 |
| 5 | "Where is the PTO policy document stored?" | Foundry — calls `askHRPolicy`; Pattern B returns the blob path |
| 6 | "Summarize PTO and tell me where it's stored" | Hybrid — one `askHRPolicy` call, answer + source |
| 7 | "What is the capital of France?" | Copilot only — declines, **no** Foundry call |

> Upload the glossary that powers paths 2–3:
> [copilot/quick_reference_guide.md](../copilot/quick_reference_guide.md) via
> **Knowledge → Add knowledge → Upload file**. A full click-by-click build is in
> [docs/CopilotStudioHybridExample.md](CopilotStudioHybridExample.md).

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

This path gives Copilot Studio access to the Foundry Agent orchestrator.
By default it runs **Pattern A — Single-Agent MCP** (`ORCHESTRATOR_PATTERN=A`):
a single Foundry Agent with `MCPTool` calls `knowledge_base_retrieve` against
the same Knowledge Base as Path 1 and returns citation-backed answers. Set
`ORCHESTRATOR_PATTERN=B` for **Pattern B — Hybrid MCP + Metadata Lookup**
(classified routing that sends file-location queries to a deterministic
metadata lookup). The 4-step sequential pipeline with source validation,
reference grounding, and GPT-4.1 answer synthesis is **optional**
(`PIPELINE_MODE=multi_step`).

> **UI Note**: Copilot Studio renamed **Actions** to **Tools** (April 2025+).
> The steps below reflect the current UI.

> **Prerequisites from Path 1**: Before adding the tool, complete Path 1
> **Step 3** (Configure Agent Instructions) and **Step 4** (Configure
> Generative AI Settings). Instructions tell the agent how to format
> responses and cite policy numbers; Generative AI settings enable
> orchestration and disable general knowledge. Path 1 **Step 2** (Add
> Azure AI Search Knowledge Source) is **optional** for Path 2 — the
> `/api/ask` tool runs its own retrieval pipeline against `hr_lab_index`.

### Step 1: Add REST API Tool (Upload specification)

> **Spec format requirement**: Copilot Studio REST API tools must be created
> from an **OpenAPI v2 (Swagger 2.0) JSON** file. If you upload a v3
> specification, Power Platform auto-translates it to v2, which can drop the
> request-body schema and the `code` query auth. To avoid that, upload the
> pre-converted Swagger 2.0 file in this repo:
> [`copilot/openapi-v2.json`](../copilot/openapi-v2.json). (The v3
> [`copilot/openapi.yaml`](../copilot/openapi.yaml) is kept as a
> human-readable reference only.)

1. Open your agent → go to the **Tools** tab
2. Click **Add a tool** → **New tool** → **REST API**
3. On **Upload specification**, drag-and-drop or browse to
   `copilot/openapi-v2.json`
4. Verify the spec details show the `askHRPolicy` operation → click **Next**

### Step 2: API plugin details (Description + Solution)

The **Description** drives tool selection by the agent's orchestration, so make
it specific and include synonyms.

| Field | Value | Source |
|---|---|---|
| Description | `Answers employee HR policy questions (PTO, leave, holiday pay, hiring, probation, code of ethics) from the HR knowledge base, including where a policy document is stored. Returns a grounded, citation-backed answer.` | Pre-filled from the spec `info.description`; expand it |
| Solution | Select an existing solution, or leave blank to auto-create one | Power Platform solution for ALM portability |

Click **Next**.

### Step 3: Configure Authentication

| Field | Value | Notes |
|---|---|---|
| Choose authentication | **API key** | Matches `securityDefinitions.functionKey` in the spec |
| Parameter label | `Function key` | Free text — the label users see when creating the connection |
| Parameter name | `code` | The query-string param the Function expects (`?code=...`) |
| Parameter location | **Query** | ⚠️ Default is **Header** — you **must** change it to **Query** |

> ⚠️ **Most common mistake**: leaving **Parameter location** as **Header**.
> Azure Functions keys are passed in the **query string** (`?code=...`), so a
> Header setting makes every call return **401 Unauthorized**. Set it to
> **Query**.
>
> You do **not** paste the secret key on this screen — it only defines *how*
> the key is sent. The actual key value is entered later at **Create
> connection** (Step 6).

Click **Next**.

### Step 4: Select Tools

Copilot Studio lists every operation in the spec. This API has one:

1. Check **`askHRPolicy`** (from the spec `operationId`)
2. Click **Next**

### Step 5: Configure tool + parameters

1. **Configure tool** — set the tool name and description:

| Field | Value | Source |
|---|---|---|
| Tool name | `Ask HR Policy` | Pre-filled from `operationId`; cosmetic |
| Tool description | `Ask an HR policy question. Runs the Foundry Agent (Pattern A single-agent MCP by default; Pattern B for file-location queries) and returns citation-backed answers.` | Pre-filled from the operation `description` |

2. **Review parameters** — types come from the spec and can't be changed, but
   **blank descriptions block Next** (all are pre-filled in `openapi-v2.json`):
   - **Input**: `query` (string, required) — the user's HR question
   - **Outputs (Pattern A/B, always present)**: `status`, `user_query`,
     `answer`, `is_grounded`, `pattern`, `pipeline_mode`
   - **Outputs (multi-step only, `PIPELINE_MODE=multi_step`)**: also `matches`,
     `references`

   > Surface the **`answer`** field in the agent's reply — it's the
   > user-facing grounded text with citation markers.

3. Click **Next**.

### Step 6: Review and Publish

1. Review the tool configuration → click **Next** / **Publish**
2. Wait for the tool to publish ("publish complete")
3. Click **Create connection** → enter your Azure Function key (the **value**
   that auth Step 3 defined the parameter for). Retrieve it with:

   ```bash
   az functionapp keys list -g rg-hr-policy-kb-lab-dev \
     -n func-hr-policy-kb-lab-dev --query "functionKeys.default" -o tsv
   ```

   Paste the output into the connection dialog (keep it private — treat it as a
   secret).
4. Select **Add and configure** to add the tool to your agent

### Step 7: Configure Tool Behavior

On the tool configuration page:

1. Under **Details** → ensure **Allow agent to decide dynamically when to use the tool** is checked
2. Under **Completion** → select **Write the response with generative AI** (lets Copilot Studio format the answer with citations)
3. Click **Save**

### Step 8: Wire the Tool in Topics (Optional)

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
4. **If using Path 2** (Foundry agent action): Copilot Studio calls `POST /api/ask` with the user's query, which runs the `FoundryAgentOrchestrator`:
   - **Pattern A — Single-Agent MCP** (`ORCHESTRATOR_PATTERN=A`, default): a single Foundry Agent with `MCPTool` and `tool_choice="required"` calls `knowledge_base_retrieve` against `hr_lab_index` (query planning → subquery decomposition → semantic ranking → answer synthesis) and returns a citation-backed answer in one pass
   - **Pattern B — Hybrid MCP + Metadata Lookup** (`ORCHESTRATOR_PATTERN=B`): a client-side classifier routes content questions to the MCP agent and file-location questions to a deterministic metadata lookup, then formats the result
   - **Optional 4-step pipeline** (`PIPELINE_MODE=multi_step`): Retrieval → Source Validation (trusted `ask-hr-knowledge` container) → Reference Validation (citations with filenames, policy numbers, paths) → Answer Synthesis
5. **Copilot Studio displays the response** with the synthesized answer and citation references
6. **Follow-up questions** continue the conversation with context

### Key Difference: Direct Search vs. Foundry Agent Pipeline

| Capability | Direct Search (Path 1) | Foundry Agent (Path 2) |
|---|---|---|
| Search type | Text + vector (integrated vectorization) + semantic ranker | Pattern A/B: single-agent MCP `knowledge_base_retrieve` (query planning + subqueries + semantic ranking + answer synthesis); Pattern B adds deterministic metadata lookup for file-location queries |
| Source validation | None | Enforced at index level (KB only searches `hr_lab_index`); explicit provenance check only in optional multi-step mode |
| Reference grounding | URL-based citations (`metadata_storage_path`) | Native MCP citation annotations with policy numbers |
| Answer synthesis | Copilot Studio built-in LLM | Foundry Agent (GPT-4.1 / configured model) over MCP-retrieved content |
| Query planning | None — single query | LLM-driven subquery decomposition (Knowledge Base) |
| Retrieval instructions | Not configurable | Configurable via `search_config.json` |
| Primary output | Built-in answer | `answer` (grounded, citation-backed) plus `status`, `is_grounded`, `pattern`, `pipeline_mode` |
| Pattern selection | n/a | `ORCHESTRATOR_PATTERN=A` (default) or `B`; 4-step pipeline optional via `PIPELINE_MODE=multi_step` |

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

### Step 0 — Baseline: verify the Foundry agent directly

Before wiring Copilot Studio, confirm the `/api/ask` endpoint runs the Foundry
agent end-to-end. This isolates Azure-side problems from Copilot Studio
configuration.

```bash
# Default Pattern A (single-agent MCP)
FUNC=https://<your-function-app>.azurewebsites.net
KEY=<function-key>   # Function App → App keys → default

curl -sS -X POST "$FUNC/api/ask?code=$KEY" \
  -H "Content-Type: application/json" \
  -d '{"query":"What is the PTO policy?"}' | jq .
```

Expected response shape (Pattern A is the default — `answer` is the field
Copilot Studio renders):

```json
{
  "status": "completed",
  "user_query": "What is the PTO policy?",
  "answer": "Paid Time Off is governed by Policy 51350 ... 【0:1†51350 - Types of Leave_ Paid Time Off (PTO)】",
  "pattern": "A",
  "pipeline_mode": "single_agent",
  "is_grounded": true
}
```

To exercise the other modes from the same endpoint, set the Function App
application settings and retest:

```bash
# Pattern B (hybrid MCP + metadata lookup) — best for file-location queries
az functionapp config appsettings set -g <rg> -n <func> \
  --settings ORCHESTRATOR_PATTERN=B
curl -sS -X POST "$FUNC/api/ask?code=$KEY" -H "Content-Type: application/json" \
  -d '{"query":"Where is the PTO policy stored?"}' | jq '.pattern, .answer'

# Optional 4-step pipeline — adds matches + references to the response
az functionapp config appsettings set -g <rg> -n <func> \
  --settings PIPELINE_MODE=multi_step
curl -sS -X POST "$FUNC/api/ask?code=$KEY" -H "Content-Type: application/json" \
  -d '{"query":"What is the PTO policy?"}' | jq '.pipeline_mode, .matches, .references'
```

> A `200` with a non-empty `answer` confirms the agent works. A `401` is auth
> (wrong/missing `code` or Easy Auth token — see [CopilotStudioAuth.md](CopilotStudioAuth.md)).
> A `500` means the orchestrator threw — check Function App logs.

---

### End-to-End Test: Copilot Studio → Foundry Agent

There are three supported ways for Copilot Studio to call the `/api/ask`
endpoint. Pick **one** based on your scenario; all three invoke the same Foundry
agent (Pattern A by default).

| Method | Auth | Best for | Reference |
|---|---|---|---|
| **A. Azure Function (REST API tool, function key)** | API key (`code` query param) | Quick lab setup, what this repo documents | [Add a REST API tool](https://learn.microsoft.com/en-us/microsoft-copilot-studio/agent-extend-action-rest-api) |
| **B. REST API tool (OAuth 2.0 / Entra ID)** | OAuth 2.0 bearer token | Production-grade auth without a static key | [Add a REST API tool](https://learn.microsoft.com/en-us/microsoft-copilot-studio/agent-extend-action-rest-api) + [CopilotStudioAuth.md](CopilotStudioAuth.md) |
| **C. Custom connector** | API key or OAuth 2.0 | Reusable, shareable connector across agents/environments | [Create a custom connector](https://learn.microsoft.com/en-us/microsoft-copilot-studio/advanced-connectors#create-a-custom-connector-to-add-to-an-agent) |

> **OpenAPI version**: Copilot Studio requires an **OpenAPI v2 (Swagger)**
> document. [copilot/openapi.yaml](../copilot/openapi.yaml) is v3 — Copilot
> Studio auto-converts v3 → v2 on upload, so upload it as-is.

#### Method A — Azure Function via REST API tool (function key)

Follows [Add a REST API tool to your agent](https://learn.microsoft.com/en-us/microsoft-copilot-studio/agent-extend-action-rest-api).

1. Open your agent → **Overview** (or **Tools** tab) → **Add tool** → **New tool** → **REST API**.
2. **Upload** [copilot/openapi.yaml](../copilot/openapi.yaml) → verify the
   `askHRPolicy` operation appears → **Next**.
3. **Description**: enter a detailed, synonym-rich description so orchestration
   knows when to call it, e.g. `Searches, finds, retrieves, and answers HR
   policy questions (PTO, leave, hiring, dress code, IT, ethics, safety) from
   the ASK HR knowledge base using the Foundry agent; returns grounded,
   citation-backed answers.` Select a **Solution** (or leave blank) → **Next**.
4. **Authentication**: select **API key**:
   - Parameter label: `Function Key`
   - Parameter name: `code`
   - Parameter location: `Query`
   - → **Next**
5. **Select tools from the API**: pick `askHRPolicy` → set **Tool name**
   `Ask HR Policy` and a detailed **Tool description** → review the input
   (`query`) and outputs (`answer`, `status`, `is_grounded`, …); fill any blank
   parameter descriptions → **Next**.
6. **Review and publish** → wait for publish → **Create connection** → enter the
   Function App **default key** → **Add and configure**.
7. On the tool's config page: **Details** → check *Allow agent to decide
   dynamically when to use the tool*; **Completion** → *Write the response with
   generative AI* → **Save**.
8. **Test** in the Test pane (see test prompts below).

#### Method B — REST API tool with OAuth 2.0 (Entra ID)

Same wizard as Method A, but at the **Authentication** step choose **OAuth 2.0**
instead of API key. This removes the static function key. Complete the Entra ID
app registration and Easy Auth setup in [CopilotStudioAuth.md](CopilotStudioAuth.md)
first, then enter:

| Field | Value |
|---|---|
| Client ID | `<APP_ID>` from the Function App app registration |
| Client secret | Secret from the app's **Certificates & secrets** blade |
| Authorization URL | `https://login.microsoftonline.com/<tenant-id>/oauth2/v2.0/authorize` |
| Token URL | `https://login.microsoftonline.com/<tenant-id>/oauth2/v2.0/token` |
| Refresh URL | `https://login.microsoftonline.com/<tenant-id>/oauth2/v2.0/token` |
| Scope | `api://<APP_ID>/.default` |

Finish the wizard (Select tools → Review → Publish → **Create connection**). On
first use, Copilot Studio triggers an admin/user consent flow. Then **Test**.

#### Method C — Custom connector

Follows [Create a custom connector to add to an agent](https://learn.microsoft.com/en-us/microsoft-copilot-studio/advanced-connectors#create-a-custom-connector-to-add-to-an-agent).

1. Open your agent → **Tools** page → **Add a tool** → **New tool** →
   **Custom connector**. Copilot Studio opens the **Power Apps** portal under
   **Custom connectors**.
2. Select **New custom connector** → **Import an OpenAPI file** → upload
   [copilot/openapi.yaml](../copilot/openapi.yaml).
3. **General** tab: confirm the **Host** is `<your-function-app>.azurewebsites.net`
   and base URL `/api`.
4. **Security** tab: choose the auth type:
   - **API key** — Parameter label `Function Key`, name `code`, location `Query`; or
   - **OAuth 2.0** → **Azure Active Directory** with the values from Method B.
5. **Definition** tab: confirm the `askHRPolicy` action, `query` input, and the
   response fields (`answer`, `status`, `is_grounded`).
6. **Create connector**, then return to Copilot Studio → **Tools** →
   **Add a tool** → **Connector** → search for your connector → **Add and
   configure** → create the connection (enter the function key or sign in).
7. **Completion** → *Write the response with generative AI* → **Save** → **Test**.

> To reuse the maker's credentials instead of prompting each user, configure an
> authenticated channel and set **Credentials to use → Maker-provided
> credentials** on the connector tool's Overview page.

---

### Test prompts (all methods)

Use the **Test** pane and confirm the agent calls the tool (not its own
knowledge):

| Prompt | Expected behavior |
|---|---|
| "What is the PTO policy?" | Cites Policy 51350; grounded answer |
| "How many holidays do we get?" | Cites Holiday Pay policy (50715) |
| "What's the dress code?" | Cites the dress code (Uniform/Non-Uniform) policy |
| "Tell me about the probationary period" | Cites Policy 50455 |
| "What is the AI/LLM policy?" | Cites the IT AI/LLM policy |
| "Where is the PTO policy stored?" | (Pattern B) returns the blob path/filename |
| "What is the capital of France?" | Says it can't help / "I don't know" (off-topic) |

### Verify Grounding
- Confirm the tool was invoked (Test pane shows the **Ask HR Policy** tool call
  and the `query` it sent).
- Check that answers include policy numbers (e.g., Policy 51350).
- Verify citations reference actual filenames from the knowledge base
  (MCP annotations render as `【message_idx:search_idx†source_name】`).
- Confirm the bot says "I don't know" / "I don't have information about that"
  for off-topic questions — this proves general knowledge is disabled and the
  answer is grounded in `hr_lab_index`.

---

## Troubleshooting

| Issue | Solution |
|---|---|
| Tool import fails / params missing | Upload the **Swagger 2.0 JSON** ([`copilot/openapi-v2.json`](../copilot/openapi-v2.json)), not the v3 YAML — Power Platform's v3→v2 auto-translation can drop the request body and auth |
| Tool never called | Improve the tool **Description** (add synonyms); confirm *Allow agent to decide dynamically* is on and generative orchestration is enabled |
| `401 Unauthorized` | Function key not sent — in auth setup, **Parameter location** must be **Query** (not Header) and **Parameter name** must be `code`. Otherwise wrong/missing function key, or Easy Auth rejected the OAuth token (audience `api://<APP_ID>`) — see [CopilotStudioAuth.md](CopilotStudioAuth.md) |
| `503 Service Unavailable` | Flex Consumption host went idle/stuck — restart: `az functionapp restart -g rg-hr-policy-kb-lab-dev -n func-hr-policy-kb-lab-dev` |
| `answer` field empty | Endpoint returned but agent produced no text — check Foundry project endpoint, model deployment, and Function App logs |
| No results returned | Verify `hr_lab_index` has documents and is queryable |
| Wrong policies cited | Check field mappings and parent metadata projections |
| Generic answers | Ensure "general knowledge" is turned off in Generative AI settings |
| Connection failed | Verify AI Search endpoint and API key |
| Foundry agent timeout | Check Azure Function health and Foundry project endpoint |

---

## References

- [Azure AI Search knowledge in Copilot Studio](https://learn.microsoft.com/en-us/microsoft-copilot-studio/knowledge-azure-ai-search)
- [Add a Foundry agent to Copilot Studio](https://learn.microsoft.com/en-us/microsoft-copilot-studio/add-agent-foundry-agent)
- [Extend your agent with tools from a REST API (preview)](https://learn.microsoft.com/en-us/microsoft-copilot-studio/agent-extend-action-rest-api)
- [Use connectors in Copilot Studio agents — Create a custom connector](https://learn.microsoft.com/en-us/microsoft-copilot-studio/advanced-connectors#create-a-custom-connector-to-add-to-an-agent)
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

### Step 3 — Query (Foundry Agent at Runtime)

Retrieval is a **query-time** feature — it is not used during indexing. Once
the Knowledge Source and Knowledge Base are provisioned (Step 2), the
`FoundryAgentOrchestrator` answers queries. By default (Pattern A) a single
Foundry Agent with an `MCPTool` calls `knowledge_base_retrieve` against the
Knowledge Base, which handles query planning, subquery decomposition, semantic
ranking, and answer synthesis. The optional multi-step mode
(`PIPELINE_MODE=multi_step`) uses the `RetrievalAgent` with
`KnowledgeBaseRetrievalClient` instead.

| Concern | Script / Component | Notes |
|---|---|---|
| Upload docs to Blob | `scripts/upload_to_blob.py` | Preserves directory structure under `data/` |
| Create index + indexer | `src/scripts/index_knowledge_base.py` | Standard indexer + skillset pipeline |
| Provision agentic retrieval | Same script (Step 5) | Knowledge Source + Knowledge Base |
| Query (default, Pattern A) | `FoundryAgentOrchestrator` → Foundry Agent `MCPTool` → `knowledge_base_retrieve` | Query-time only |
| Query (optional multi-step) | `RetrievalAgent` → `KnowledgeBaseRetrievalClient` | `PIPELINE_MODE=multi_step` |
