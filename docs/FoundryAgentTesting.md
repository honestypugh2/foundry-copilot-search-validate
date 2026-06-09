# Testing the Foundry Agent & Viewing Results in the Foundry Portal

This guide covers how to test the HR Policy Foundry Agent pipeline locally
and observe the results — including agent traces, threads, and model
activity — in the [Microsoft Foundry portal](https://ai.azure.com).

---

## Prerequisites

| Requirement | Value |
|---|---|
| Azure CLI authenticated | `az login` with correct subscription selected |
| Environment variables | `.env` file sourced (see below) |
| Python dependencies | `uv sync` completed |
| Index populated | `hr_lab_index` with documents (run `index_knowledge_base.py`) |
| Knowledge Base provisioned | `hr-knowledge-base` + `hr-knowledge-source` (Step 5 of indexing script) |

### Required Environment Variables

```bash
# Azure AI Search
AZURE_SEARCH_ENDPOINT=https://<your-search-service>.search.windows.net
AZURE_SEARCH_INDEX=<your-index-name>

# Azure OpenAI
AZURE_OPENAI_ENDPOINT=https://<your-openai-resource>.openai.azure.com/openai/v1
GPT_DEPLOYMENT_NAME=<your-deployment-name>
GPT_MODEL_NAME=<your-model-name>

# Azure AI Foundry (required for Steps 2–4: Source Validator, Reference Validator, Answer Synthesis)
AZURE_AI_PROJECT_ENDPOINT=https://<your-ai-service>.services.ai.azure.com/api/projects/<your-project>
```

> **Note**: `AZURE_AI_PROJECT_ENDPOINT` is required for the Foundry Agent
> steps (source validation, reference validation, answer synthesis fallback).
> Agentic retrieval (Step 1) uses `AZURE_SEARCH_ENDPOINT` only.

---

## Test 1: Agentic Retrieval Only (Step 1)

Tests the Knowledge Base retrieval pipeline without the Foundry Agent steps.
This verifies query planning, subquery decomposition, semantic ranking,
and answer synthesis via the Knowledge Base's GPT-4.1 model.

```bash
cd $PROJECT_ROOT
source .env

PYTHONPATH=$PWD/src uv run python -c "
from src.search.azure_ai_search_client import AzureAISearchClient

client = AzureAISearchClient()
messages = [{'role': 'user', 'content': 'What is the PTO policy?'}]
result = client.agentic_retrieve(messages)

print('=== RESPONSE ===')
print(result['response'][:800])
print()
print(f'=== REFERENCES ({len(result[\"matches\"])}) ===')
for m in result['matches']:
    print(f'  - {m[\"fileName\"]} (score: {m[\"reranker_score\"]})')
print()
print(f'=== ACTIVITY ({len(result[\"activity\"])}) ===')
for a in result['activity']:
    print(f'  Step {a[\"id\"]}: {a[\"type\"]} ({a.get(\"elapsed_ms\", \"?\")}ms)')
"
```

**Expected output**: A synthesized answer about Policy 51350 (Paid Time Off)
with references and a multi-step activity trace showing `modelQueryPlanning`
→ `searchIndex` → answer synthesis.

---

## Test 2: Full Sequential Pipeline (Multi-Step, `PIPELINE_MODE=multi_step`)

> **Note:** This tests the legacy 4-step pipeline, not the default single-agent MCP mode.
> Set `PIPELINE_MODE=multi_step` before running. The default `single_agent` mode is tested
> via `test_mcp_query_retrieval.py`.

Tests the complete Foundry Agent pipeline: agentic retrieval → source
validation → reference validation → answer synthesis.

```bash
cd $PROJECT_ROOT
source .env

PYTHONPATH=$PWD/src uv run python -c "
import asyncio
import json
from agents.sequential_orchestrator_foundry import FoundryAgentOrchestrator

async def main():
    orchestrator = FoundryAgentOrchestrator()
    result = await orchestrator.process_query_async('What is the holiday pay policy?')

    print('=== PIPELINE RESULT ===')
    print(f'Status: {result.get(\"status\", \"unknown\")}')
    print()

    # Answer
    answer = result.get('answer', '')
    print(f'=== ANSWER ===')
    print(answer[:800] if answer else '(no answer)')
    print()

    # Pipeline steps
    steps = result.get('steps', [])
    print(f'=== PIPELINE STEPS ({len(steps)}) ===')
    for step in steps:
        name = step.get('name', '?')
        status = step.get('status', '?')
        duration = step.get('duration_ms', '?')
        print(f'  {name}: {status} ({duration}ms)')
    print()

    # Grounding
    print(f'Grounded: {result.get(\"is_grounded\", \"unknown\")}')
    print(f'Sources:  {len(result.get(\"validated_sources\", []))}')
    print(f'Refs:     {len(result.get(\"references\", []))}')

asyncio.run(main())
"
```

**Expected output**: All 4 steps complete with `status: success`. The answer
is grounded with validated sources and structured references including policy
numbers.

---

## Test 3: Individual Foundry Agents

### Source Validator Agent (Step 2)

```bash
PYTHONPATH=$PWD/src uv run python -c "
from agents.source_validator_agent import SourceValidatorAgent

agent = SourceValidatorAgent()
# Simulate matches from Step 1
matches = [
    {'content': 'PTO policy content...', 'fileName': '51350.docx',
     'container': 'ask-hr-knowledge', 'filePath': 'knowledge_base_lab/51350.docx'},
    {'content': 'Untrusted doc', 'fileName': 'random.pdf',
     'container': 'other-container', 'filePath': 'other/random.pdf'},
]
result = agent.run(matches)
print(f'Validated sources: {result[\"source_count\"]}')
for src in result['validated_sources']:
    print(f'  - {src[\"fileName\"]} (trusted: {src.get(\"trust_assessment\", \"?\")})')
"
```

### Answer Synthesis Agent (Step 4)

```bash
PYTHONPATH=$PWD/src uv run python -c "
from agents.answer_synthesis_agent import AnswerSynthesisAgent

agent = AnswerSynthesisAgent()

# With agentic answer (pass-through, no LLM call)
result = agent.run(
    user_query='What is the PTO policy?',
    validated_sources=[{'content': 'PTO allows...', 'fileName': '51350.docx'}],
    references=[{'policyNumber': '51350', 'parentTitle': 'PTO'}],
    is_grounded=True,
    agentic_answer='PTO is governed by Policy 51350...',
)
print(f'Source: {result[\"source\"]}')  # 'agentic_retrieval'
print(f'Answer: {result[\"answer\"][:200]}')

# Without agentic answer (Foundry Agent LLM call)
result2 = agent.run(
    user_query='What is the PTO policy?',
    validated_sources=[{'content': 'PTO allows 15 days per year...', 'fileName': '51350.docx'}],
    references=[{'policyNumber': '51350', 'parentTitle': 'PTO'}],
    is_grounded=True,
    agentic_answer=None,
)
print(f'Source: {result2.get(\"source\", \"foundry_agent\")}')
print(f'Answer: {result2[\"answer\"][:200]}')
"
```

---

## Test 4: Run the Integration Test Suite

The project includes an end-to-end test that exercises the full pipeline:

```bash
cd $PROJECT_ROOT
source .env

PYTHONPATH=$PWD/src uv run python tests/test_full_flow.py
```

Add `--mock` to run without Azure credentials (uses mock data):

```bash
PYTHONPATH=$PWD/src uv run python tests/test_full_flow.py --mock
```

---

## Test 4b: MCP Query Retrieval — Pattern A & B

The primary test for the production pipelines. Supports both orchestrator patterns:

```bash
cd $PROJECT_ROOT
source .env

# Pattern A: single-agent MCP (default)
PYTHONPATH=$PWD/src python tests/test_mcp_query_retrieval.py --pattern A --query_id=21

# Pattern B: hybrid MCP + metadata lookup
PYTHONPATH=$PWD/src python tests/test_mcp_query_retrieval.py --pattern B --query_id=21

# Run all queries for a pattern
PYTHONPATH=$PWD/src python tests/test_mcp_query_retrieval.py --pattern B

# Run first N queries
PYTHONPATH=$PWD/src python tests/test_mcp_query_retrieval.py --pattern A --limit 5
```

**What it validates:**
- Azure environment + credentials (Step 1)
- SDK imports (Step 2)
- Orchestrator initialization via factory (Step 3)
- Live query execution with policy/file assertions, token usage, and activity tracking (Step 4)
- Results written to `tests/mcp_query_retrieval_results.json` (Step 5)

**Pattern B specific behavior:**
- Client-side query classification detects file-location intent (regex-based)
- File-location queries → `file_metadata_lookup` called directly (bypasses MCP),
  results formatted by the `HRPolicyAgentB-FileLocation` agent (no tools)
- Content queries → `HRPolicyAgentB` (Foundry Agent + MCPTool) — platform handles
  MCP natively, same retrieval semantics as Pattern A
- Two stable agent names instead of one prevent the portal from showing churn
  between runs (see `_ensure_foundry_agent` in `src/agents/foundry_client.py`)
- Activity data (subqueries, elapsed times) captured from the MCP content pipeline

---

## Test 5: Sample HR Policy Questions

Use these questions to test various policy areas. Expected results should
return the synthesized answer **plus** metadata: `fileName`, `policyNumber`,
`parentTitle`, `blob_url`, `filePath`, and `container`.

### Exact Document Lookups (by policy number)

Like asking "Find the DFMEA for ST299":

| Query | Expected File | Expected Metadata |
|---|---|---|
| "Find Policy 51350 on Paid Time Off" | `51350 - Types of Leave_ Paid Time Off (PTO) (23472_2).docx` | policyNumber contains `51350`, container=`ask-hr-knowledge` |
| "Show me Policy 52005 for the Uniform Dress Code" | `52005 - Operational Matters_ Uniform Dress Code (2583_19).docx` | policyNumber contains `52005` |
| "Pull up document 87100 about the AI and LLM policy" | `87100 - Generative Artificial Intelligence (AI) & Large Language Models (LLM) Policy (23653_5).docx` | policyNumber contains `87100` |
| "Find the IT Acceptable Use Policy number 81100" | `81100 - Information Technology Acceptable Use Policy (23666_5).docx` | policyNumber contains `81100` |
| "Find Policy 88100 on Mobile Device use" | `88100 - Mobile Device and Use Policy (23693_3).docx` | policyNumber contains `88100` |

### Topic / Title Lookups (no policy number in query)

Like asking "Show me results of ON/OFF testing done for FM300":

| Query | Expected File | Expected Metadata |
|---|---|---|
| "Show me the Short-Term Disability policy" | `51370 - Short-Term Disability (23317_1).docx` | policyNumber contains `51370` |
| "Find the SOP for Uniform Issuance" | `SOP - Uniform Issuance (23686_3).docx` | parentTitle contains `SOP - Uniform Issuance` |
| "What does the Emergency Notification System policy say?" | `83400 - Emergency Notification System Policy (23234_0).doc` | policyNumber contains `83400` |
| "What are the pre-employment medical examination requirements?" | `50410 - Hiring_ Pre-employment Medical Examinations (23290_2).docx` | policyNumber contains `50410` |

### Natural Language Questions

Like asking "What is the PTO policy?":

| Query | Expected File | Expected Metadata |
|---|---|---|
| "What is the PTO policy?" | `51350 - Types of Leave_ Paid Time Off (PTO) (23472_2).docx` | policyNumber contains `51350` |
| "What is the holiday pay policy?" | `50715 - Hours Worked and Pay Administration_ Holiday Pay (23641_4).docx` | policyNumber contains `50715` |
| "What is the Code of Ethics?" | `31000 - Code of Ethics and Related Matters (9081_12).docx` | policyNumber contains `31000` |
| "What is the career path for an HR Generalist?" | `50815 - Career Path_ HR Generalist (19791_3).docx` | policyNumber contains `50815` |

### Disambiguation Queries (must pick the RIGHT document)

| Query | Expected File | Should NOT Return |
|---|---|---|
| "What is the PTO accrual rate for part-time employees?" | `51355 - Types of Leave_ Paid Time Off (PTO) - Part-time (23315_2).docx` | Should be 51355, not 51350 |
| "Show me the Non-Uniform Dress Code policy" | `52010 - Operational Matters_ Non-Uniform Dress Code (23685_1).docx` | Should be 52010, not 52005 |
| "Find the IT Information Security Policy 83100" | `83100 - IT Information Security Policy (23806_3).docx` | Should be 83100, not 81100 |

### Expected Result Structure

Every result should include these metadata fields in each match:

```json
{
  "content": "# Policy 51350 — Types of Leave_ Paid Time Off (PTO)...",
  "fileName": "51350 - Types of Leave_ Paid Time Off (PTO) (23472_2).docx",
  "filePath": "https://<your-storage>.blob.core.windows.net/<your-container>/knowledge_base_lab/...",
  "parentTitle": "51350 - Types of Leave_ Paid Time Off (PTO) (23472_2).docx",
  "policyNumber": "51350 - Types of Leave_ Paid Time Off (PTO) (23472_2).docx",
  "container": "<your-container>",
  "reranker_score": 3.30,
  "blob_url": "https://<your-storage>.blob.core.windows.net/<your-container>/knowledge_base_lab/..."
}
```

---

## Viewing Results in the Microsoft Foundry Portal

After running tests that invoke Foundry Agents (Steps 2–4), you can see the
agent threads, model calls, and traces in the portal.

### 1. Open the Foundry Portal

Navigate to your project:

```
https://ai.azure.com
```

1. Sign in with your Azure credentials
2. Select your AI Foundry resource (`<your-ai-foundry-resource>`)
3. Select your project (`<your-project>`)

### 2. View Agent Threads & Runs

1. In the left nav, go to **Agents**
2. You will see the persistent agents:
   - `HRPolicyAgent` (Pattern A — single-agent MCP)
   - `HRPolicyAgentB` (Pattern B — content/MCP path)
   - `HRPolicyAgentB-FileLocation` (Pattern B — formatting agent for file-location queries)

> **Note**: Agents are persistent and reused across runs. The orchestrator
> calls `_ensure_foundry_agent()` ([`src/agents/foundry_client.py`](../src/agents/foundry_client.py))
> which performs **get-or-create**: it reuses the existing version when
> one exists, so the portal does not prompt "Save the Agent" between runs.
> Set `RECREATE_FOUNDRY_AGENTS=true` to mint a fresh version on each call.
> To inspect run history, use **Tracing** (see below).

### 3. View Tracing & Telemetry

1. In the left nav, go to **Tracing**
2. Select a recent trace to see the full request lifecycle:
   - Model invocations (GPT-4.1 calls)
   - Input/output token counts
   - Latency per step
   - Request/response payloads

> **Prerequisites for the Tracing tab to populate**:
>
> 1. **App Insights connection on the Foundry project** — a
>    `Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01`
>    resource of `category: 'AppInsights'`. The default lab Bicep
>    ([`infra/bicep/main.bicep`](../infra/bicep/main.bicep)) creates one.
>    For an existing project, deploy
>    [`infra/bicep/connect-appinsights.bicep`](../infra/bicep/connect-appinsights.bicep).
> 2. **`APPLICATIONINSIGHTS_CONNECTION_STRING`** in `.env` — the runtime
>    needs this to ship spans. The orchestrators call `enable_tracing()`
>    from [`src/observability.py`](../src/observability.py) at module
>    load, which wires up `azure-monitor-opentelemetry`.
> 3. **`AZURE_EXPERIMENTAL_ENABLE_GENAI_TRACING=true`** — set as a default
>    by `enable_tracing()`. **Required** for the Azure AI Projects SDK to
>    emit agent / tool spans. Without it the tab stays empty even when
>    everything else is wired up.
> 4. **Install the OTel dep with prereleases enabled**:
>    `uv pip install --prerelease=allow 'azure-monitor-opentelemetry>=1.6.0,<2'`.

### 4. View Model Deployments

1. Go to **Model catalog** → **Deployments** (or **Models + endpoints**)
2. Verify your deployments:
   - `gpt-4.1` — used by all Foundry Agents and Knowledge Base answer synthesis
   - `text-embedding-3-small` — used by the indexer skillset for vector embeddings

### 5. View Knowledge Base (Agentic Retrieval)

1. In the Azure portal (not Foundry portal), go to your **Azure AI Search** service
2. Navigate to **Knowledge sources** → `hr-knowledge-source`
3. Navigate to **Knowledge bases** → `hr-knowledge-base`
4. Here you can:
   - View the Knowledge Base configuration (model, instructions, output mode)
   - Edit the Knowledge Source field mappings
   - Test retrieval queries directly from the portal

### 6. Monitor via Azure Portal

For detailed monitoring of the Search + Foundry pipeline:

| What | Where |
|---|---|
| Search index stats | Azure Portal → AI Search → Indexes → `hr_lab_index` |
| Indexer run history | Azure Portal → AI Search → Indexers → `hr-lab-index-indexer` |
| Knowledge Base config | Azure Portal → AI Search → Knowledge bases → `hr-knowledge-base` |
| Agent model usage | Foundry Portal → Metrics |
| Request tracing | Foundry Portal → Tracing (requires App Insights) |
| Function App logs | Azure Portal → Function App → Monitor → Log stream |

---

## Troubleshooting

| Issue | Cause | Solution |
|---|---|---|
| `AZURE_AI_PROJECT_ENDPOINT not set` | Missing env var | Set `AZURE_AI_PROJECT_ENDPOINT` in `.env` |
| `azure-ai-projects SDK not installed` | Missing dependency | Run `uv sync` |
| `AGENTIC_RETRIEVAL_AVAILABLE` is False | SDK too old | Ensure `azure-search-documents>=11.7.0b2` |
| Agent returns empty answer | Thread/run issue | Check Foundry portal Tracing for errors |
| Agentic retrieval 404 | Semantic ranker not enabled | `az search service update --semantic-search free` |
| "Could not complete model action" | KB model config | Verify `gpt-4.1` deployment exists, Search MI has `Cognitive Services User` role |
| No traces in Foundry portal | App Insights not connected | Set `AZURE_APPLICATION_INSIGHTS_CONNECTION_STRING` |
| Source validator rejects all | Container mismatch | Ensure blobs are in `ask-hr-knowledge` container |

---

## Quick Smoke Test (Copy-Paste)

Run all key tests in sequence:

```bash
cd $PROJECT_ROOT
source .env
export AZURE_SEARCH_ENDPOINT AZURE_OPENAI_ENDPOINT AZURE_AI_SERVICES_ENDPOINT \
       AZURE_SEARCH_INDEX GPT_DEPLOYMENT_NAME GPT_MODEL_NAME \
       EMBEDDING_DEPLOYMENT_NAME EMBEDDING_MODEL_NAME AZURE_AI_PROJECT_ENDPOINT

# 1. Verify agentic retrieval
echo "=== Test 1: Agentic Retrieval ==="
PYTHONPATH=$PWD/src uv run python -c "
from src.search.azure_ai_search_client import AzureAISearchClient
client = AzureAISearchClient()
result = client.agentic_retrieve([{'role': 'user', 'content': 'What is the PTO policy?'}])
print(f'Response length: {len(result[\"response\"])} chars')
print(f'References: {len(result[\"matches\"])}')
print(f'Activity steps: {len(result[\"activity\"])}')
print('PASS' if result['response'] and '51350' in result['response'] else 'REVIEW')
"

# 2. Verify full pipeline
echo ""
echo "=== Test 2: Full Sequential Pipeline ==="
PYTHONPATH=$PWD/src uv run python -c "
import asyncio
from agents.sequential_orchestrator_foundry import FoundryAgentOrchestrator
async def main():
    o = FoundryAgentOrchestrator()
    r = await o.process_query_async('How many holidays do we get?')
    print(f'Status: {r.get(\"status\", \"unknown\")}')
    print(f'Grounded: {r.get(\"is_grounded\", False)}')
    print(f'Answer: {r.get(\"answer\", \"\")[:200]}')
    has_holiday = 'holiday' in r.get('answer', '').lower() or '50715' in r.get('answer', '')
    print('PASS' if r.get('is_grounded') and has_holiday else 'REVIEW')
asyncio.run(main())
"

echo ""
echo "=== Done. Check Foundry Portal → Tracing for agent activity ==="
```
