# Testing the Agent Framework Pipeline & Viewing Results in the Foundry Portal

This guide covers how to test the HR Policy Agent Framework pipeline locally
and observe the results — including agent traces, FoundryChatClient activity,
and SequentialBuilder workflow events — in the [Microsoft Foundry portal](https://ai.azure.com).

---

## Prerequisites

| Requirement | Value |
|---|---|
| Azure CLI authenticated | `az login` with correct subscription selected |
| Environment variables | `.env` file sourced (see below) |
| Python dependencies | `uv sync` completed |
| Index populated | `hr_lab_index` with documents (run `index_knowledge_base.py`) |
| Knowledge Base provisioned | `hr-knowledge-base` + `hr-knowledge-source` (Step 5 of indexing script) |
| Agent Framework packages | `agent-framework`, `agent-framework-foundry`, `agent-framework-orchestrations` |

### Required Environment Variables

```bash
# Azure AI Search
AZURE_SEARCH_ENDPOINT=https://<your-search-service>.search.windows.net
AZURE_SEARCH_INDEX_NAME=hr_lab_index

# Azure OpenAI
AZURE_OPENAI_ENDPOINT=https://<your-openai-resource>.openai.azure.com/openai/v1

# Azure AI Foundry (required for FoundryChatClient Agent)
AZURE_AI_PROJECT_ENDPOINT=https://<your-ai-service>.services.ai.azure.com/api/projects/<your-project>
# or
FOUNDRY_PROJECT_ENDPOINT=https://<your-ai-service>.services.ai.azure.com

# Model
FOUNDRY_MODEL=gpt-4.1
```

> **Note**: `AZURE_AI_PROJECT_ENDPOINT` or `FOUNDRY_PROJECT_ENDPOINT` is required
> for the FoundryChatClient Agent (answer synthesis). Agentic retrieval (Step 1)
> uses `AZURE_SEARCH_ENDPOINT` only.

---

## Architecture Overview

The Agent Framework pipeline uses `SequentialBuilder` to chain custom `Executor`s
(deterministic logic) with a `FoundryChatClient`-backed `Agent` (LLM inference):

```
User Query
  → RetrievalExecutor          (custom Executor — agentic retrieval / hybrid search)
  → SourceValidationExecutor   (custom Executor — deterministic container filter)
  → ReferenceValidationExecutor (custom Executor — deterministic citation extraction)
  → AnswerSynthesisExecutor    (custom Executor — routes: agentic passthrough or → Agent)
  → FoundryChatClient Agent    (LLM synthesis — only invoked when no agentic answer)
  → FinalSynthesisExecutor     (custom Executor — captures final output)
```

When agentic retrieval provides a pre-synthesized answer, the FoundryChatClient
Agent step is effectively skipped (the answer passes through).

---

## Test 1: Agentic Retrieval Only (Step 1)

Tests the Knowledge Base retrieval pipeline independently of the Agent Framework
orchestration. Verifies query planning, subquery decomposition, semantic ranking,
and answer synthesis via the Knowledge Base.

```bash
cd $PROJECT_ROOT
source .env

PYTHONPATH=$PWD/src uv run python -c "
from agents_af.retrieval_agent import RetrievalAgent

agent = RetrievalAgent()
print(f'agentic_enabled: {agent._agentic_enabled}')

result = agent.run('What is the PTO policy?')

print()
print(f'=== MATCHES ({len(result[\"matches\"])}) ===')
for m in result['matches']:
    print(f'  - {m[\"fileName\"]} (policy: {m[\"policyNumber\"]})')

if 'agentic_answer' in result:
    print()
    print('=== AGENTIC ANSWER ===')
    print(result['agentic_answer'][:500])

if 'activity' in result:
    print()
    print(f'=== ACTIVITY ({len(result[\"activity\"])}) ===')
    for a in result['activity']:
        print(f'  {a}')
"
```

**Expected output**: Matches containing Policy 51350 (Paid Time Off) with an
agentic answer (if agentic retrieval is available) or hybrid search results.

---

## Test 2: Full Sequential Pipeline (SequentialBuilder)

Tests the complete Agent Framework pipeline through all 6 participants.

```bash
cd $PROJECT_ROOT
source .env

PYTHONPATH=$PWD/src uv run python -c "
from agents_af.sequential_orchestrator import SequentialWorkflowOrchestrator

orch = SequentialWorkflowOrchestrator()
result = orch.process_query('What is the holiday pay policy?')

print('=== PIPELINE RESULT ===')
print(f'Status:    {result.get(\"status\", \"unknown\")}')
print(f'Grounded:  {result.get(\"is_grounded\", False)}')
print(f'Matches:   {len(result.get(\"matches\", []))}')
print(f'Sources:   {len(result.get(\"validated_sources\", []))}')
print(f'Refs:      {len(result.get(\"references\", []))}')
print()
print('=== ANSWER ===')
print(result.get('answer', '(no answer)')[:500])
print()
print('=== STEPS ===')
for name, step in result.get('steps', {}).items():
    print(f'  {name}: {step.get(\"status\", \"?\")}'
          + (f' ({step.get(\"match_count\", \"\")})'
             if 'match_count' in step else ''))
"
```

**Expected output**: All steps complete with `status: completed`. The answer
references Policy 50715 (Holiday Pay) with grounded sources.

---

## Test 3: Individual Agent Framework Agents

### RetrievalAgent (Step 1)

```bash
PYTHONPATH=$PWD/src uv run python -c "
from agents_af.retrieval_agent import RetrievalAgent

agent = RetrievalAgent()
result = agent.run('Find Policy 52005 for the Uniform Dress Code')

print(f'Matches: {len(result[\"matches\"])}')
print(f'Agentic: {\"agentic_answer\" in result}')
for m in result['matches'][:3]:
    print(f'  - {m[\"fileName\"]} (policy: {m[\"policyNumber\"]}, container: {m[\"container\"]})')
"
```

### SourceValidatorAgent (Step 2)

```bash
PYTHONPATH=$PWD/src uv run python -c "
from agents_af.source_validator_agent import SourceValidatorAgent

agent = SourceValidatorAgent()
# Simulate matches with mixed containers
matches = [
    {'content': 'PTO policy content...', 'fileName': '51350.docx',
     'container': 'ask-hr-knowledge', 'filePath': 'knowledge_base_lab/51350.docx',
     'parentTitle': '51350 - PTO', 'policyNumber': '51350', 'documentType': 'pdf'},
    {'content': 'Untrusted doc', 'fileName': 'random.pdf',
     'container': 'other-container', 'filePath': 'other/random.pdf',
     'parentTitle': '', 'policyNumber': '', 'documentType': 'pdf'},
]
result = agent.run({'matches': matches})
print(f'Input:     {len(matches)} matches')
print(f'Validated: {result[\"source_count\"]}')
for src in result['validated_sources']:
    print(f'  ✓ {src[\"fileName\"]} (container: {src[\"container\"]})')
"
```

### ReferenceValidatorAgent (Step 3)

```bash
PYTHONPATH=$PWD/src uv run python -c "
from agents_af.reference_validator_agent import ReferenceValidatorAgent

agent = ReferenceValidatorAgent()
validated_sources = [
    {'content': 'PTO accrued at 1.5 days/month',
     'filePath': 'https://storage.blob.core.windows.net/ask-hr-knowledge/51350.pdf',
     'fileName': '51350 - Types of Leave_ Paid Time Off (PTO) (23472_2).docx',
     'parentTitle': '51350 - Types of Leave_ Paid Time Off (PTO) (23472_2).docx',
     'policyNumber': '51350', 'documentType': 'pdf', 'container': 'ask-hr-knowledge'},
]
result = agent.run({'validated_sources': validated_sources})
print(f'Grounded:   {result[\"is_grounded\"]}')
print(f'References: {len(result[\"references\"])}')
for ref in result['references']:
    print(f'  - Policy {ref[\"policyNumber\"]}: {ref[\"fileName\"]}')
"
```

### AnswerSynthesisAgent (Step 4)

```bash
PYTHONPATH=$PWD/src uv run python -c "
from agents_af.answer_synthesis_agent import AnswerSynthesisAgent

agent = AnswerSynthesisAgent()
print(f'Model:       {agent.model}')
print(f'Output mode: {agent.output_mode}')

# Test 1: Agentic answer passthrough (no LLM call)
result = agent.run(
    user_query='What is the PTO policy?',
    validated_sources=[{'content': 'PTO allows 15 days/year', 'fileName': '51350.docx',
                        'container': 'ask-hr-knowledge'}],
    references=[{'policyNumber': '51350', 'parentTitle': 'PTO',
                 'filePath': '/p', 'documentType': 'pdf', 'fileName': '51350.pdf'}],
    is_grounded=True,
    agentic_answer='PTO is governed by Policy 51350. Employees accrue 1.5 days/month.',
)
print(f'\\nPassthrough test:')
print(f'  Source: {result[\"source\"]}')
print(f'  Answer: {result[\"answer\"][:100]}')

# Test 2: No agentic answer — pending for FoundryChatClient
result2 = agent.run(
    user_query='What is the PTO policy?',
    validated_sources=[{'content': 'PTO content...', 'fileName': '51350.docx',
                        'container': 'ask-hr-knowledge'}],
    references=[{'policyNumber': '51350', 'parentTitle': 'PTO',
                 'filePath': '/p', 'documentType': 'pdf', 'fileName': '51350.pdf'}],
    is_grounded=True,
)
print(f'\\nPending test:')
print(f'  Source:  {result2[\"source\"]}')
print(f'  Context: {len(result2[\"context\"])} chars (forwarded to FoundryChatClient)')
"
```

---

## Test 4: Custom Executor Data Flow (Unit Tests)

The Agent Framework Executors pass state as serialized JSON in `Message` objects.
Test their wiring in isolation:

```bash
cd $PROJECT_ROOT
source .env

PYTHONPATH=$PWD/src uv run python -m pytest tests/test_agents_af.py --mock -v -k "TestExecutorsAF"
```

This validates:
- `RetrievalExecutor` correctly forwards `user_query` + `retrieval_output` as state
- `SourceValidationExecutor` filters and forwards `validation_output`
- `ReferenceValidationExecutor` extracts references and checks grounding
- `AnswerSynthesisExecutor` routes to passthrough or FoundryChatClient

---

## Test 5: Run the Full Test Suite

### Mock mode (no Azure required)

```bash
cd $PROJECT_ROOT
PYTHONPATH=$PWD/src uv run python -m pytest tests/test_agents_af.py --mock -v
```

### Live mode (requires Azure credentials)

```bash
cd $PROJECT_ROOT
source .env
PYTHONPATH=$PWD/src uv run python -m pytest tests/test_agents_af.py -v -s
```

### End-to-End Integration Test

```bash
cd $PROJECT_ROOT
source .env

# Live mode (no mocks — requires Azure credentials)
PYTHONPATH=$PWD/src uv run python tests/e2e_live_agent_framework.py
```

> The end-to-end script is live-only. For mock/wiring validation use the unit
> test instead: `PYTHONPATH=$PWD/src uv run python -m pytest tests/test_agents_af.py --mock -v`.

---

## Test 6: Sample HR Policy Questions

Use these queries to validate the pipeline across different policy areas:

### Exact Document Lookups (by policy number)

| Query | Expected Policy | Expected File |
|---|---|---|
| "Find Policy 51350 on Paid Time Off" | 51350 | `51350 - Types of Leave_ Paid Time Off (PTO) (23472_2).docx` |
| "Show me Policy 52005 for the Uniform Dress Code" | 52005 | `52005 - Operational Matters_ Uniform Dress Code (2583_19).docx` |
| "Pull up document 87100 about the AI and LLM policy" | 87100 | `87100 - Generative Artificial Intelligence (AI) & Large Language Models (LLM) Policy (23653_5).docx` |
| "Find the IT Acceptable Use Policy number 81100" | 81100 | `81100 - Information Technology Acceptable Use Policy (23666_5).docx` |

### Topic / Title Lookups (no policy number in query)

| Query | Expected Policy | Expected File |
|---|---|---|
| "Show me the Short-Term Disability policy" | 51370 | `51370 - Short-Term Disability (23317_1).docx` |
| "What does the Emergency Notification System policy say?" | 83400 | `83400 - Emergency Notification System Policy (23234_0).doc` |
| "Find the SOP for Uniform Issuance" | (none) | `SOP - Uniform Issuance (23686_3).docx` |

### Natural Language Questions

| Query | Expected Policy | Expected File |
|---|---|---|
| "What is the PTO policy?" | 51350 | `51350 - Types of Leave_ Paid Time Off (PTO) (23472_2).docx` |
| "What is the holiday pay policy?" | 50715 | `50715 - Hours Worked and Pay Administration_ Holiday Pay (23641_4).docx` |
| "What is the Code of Ethics?" | 31000 | `31000 - Code of Ethics and Related Matters (9081_12).docx` |

---

## Viewing Results in the Microsoft Foundry Portal

After running tests that invoke the FoundryChatClient Agent, you can observe
agent activity in the portal.

### 1. Open the Foundry Portal

Navigate to your project:

```
https://ai.azure.com
```

1. Sign in with your Azure credentials
2. Select your AI Foundry resource
3. Select your project

### 2. View Agent Registrations

1. In the left nav, go to **Agents**
2. The `hr-answer-synthesis` agent is registered during orchestrator initialization
3. You will see:
   - Agent name: `hr-answer-synthesis`
   - Model: `gpt-4.1`
   - Version history (one version per deployment)

> **Note**: The agent is registered via `_ensure_foundry_agent()` in
> [`src/agents/foundry_client.py`](../src/agents/foundry_client.py), which
> wraps `AIProjectClient.agents.get()` + `agents.create_version()`. The
> default behaviour is **get-or-create**: an existing agent version is
> reused so the portal does not flag every run as a draft. Set
> `RECREATE_FOUNDRY_AGENTS=true` to force a new version on every call.
> If registration fails (401), the pipeline still works — it just won't
> appear in the portal.

### 3. View Tracing & Telemetry

1. In the left nav, go to **Tracing**
2. Select a recent trace to see:
   - FoundryChatClient API calls (Responses endpoint)
   - Input/output token counts
   - Latency per model invocation
   - Full request/response payloads

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

### 4. Understand the Workflow Event Stream

The `SequentialBuilder` emits events during execution. When running with
logging, you'll see:

```
Step 1: Retrieval Agent
✓ Retrieval complete: 5 matches
Step 2: Source Validation Agent
✓ Source validation complete: 5/5 sources trusted
Step 3: Reference Validation Agent
✓ Reference validation complete: grounded=True, references=5
Step 4: Answer Synthesis
✓ Answer synthesis complete (source=agentic_retrieval)
```

When agentic retrieval provides an answer, you'll see `source=agentic_retrieval`.
When the FoundryChatClient Agent is invoked, you'll see `source=foundry_chat_client`.

---

## Agent Framework vs Foundry Agent Service

| Aspect | Agent Framework (this path) | Foundry Agent Service |
|---|---|---|
| **Orchestration** | `SequentialBuilder` (built-in) | Python async/await |
| **LLM client** | `FoundryChatClient` (Responses API) | `AIProjectClient` → conversations |
| **Deterministic steps** | Custom `Executor` classes | Python functions |
| **Agent persistence** | Ephemeral per workflow | Persistent in Foundry Portal |
| **Conversation history** | Automatic (AF manages `Message` lists) | Manual (`conversations.create/delete`) |
| **Streaming** | Built-in via SequentialBuilder events | Manual `async for event in stream` |
| **Test command** | `pytest tests/test_agents_af.py --mock -v` | `pytest tests/test_agents_foundry.py --mock -v` |

---

## Troubleshooting

| Issue | Cause | Solution |
|---|---|---|
| `ModuleNotFoundError: agent_framework` | Package not installed | `uv add agent-framework agent-framework-foundry agent-framework-orchestrations` |
| `FOUNDRY_PROJECT_ENDPOINT not set` | Missing env var | Set `FOUNDRY_PROJECT_ENDPOINT` or `AZURE_AI_PROJECT_ENDPOINT` in `.env` |
| `Could not register agent in Foundry portal` | 401 Unauthorized | Run `az login` or check RBAC role (needs `Azure AI Developer`) |
| Agentic answer always passes through | Knowledge Base returns a response | Expected — this is the optimal path (no extra LLM call) |
| FoundryChatClient Agent returns empty | Token limit or model error | Check Foundry Portal → Tracing for error details |
| `SequentialBuilder` hangs | Executor didn't call `ctx.send_message()` | Verify each executor forwards messages correctly |
| `AGENTIC_RETRIEVAL_AVAILABLE` is False | SDK too old | Ensure `azure-search-documents>=11.7.0b2` |
| Test fails with `mock_chat.as_agent` error | Agent Framework version mismatch | Verify `agent-framework-foundry` is installed |

---

## Quick Smoke Test (Copy-Paste)

Run all key tests in sequence:

```bash
cd $PROJECT_ROOT
source .env
export AZURE_SEARCH_ENDPOINT AZURE_OPENAI_ENDPOINT AZURE_AI_PROJECT_ENDPOINT

# 1. Verify imports
echo "=== Test 1: Imports ==="
PYTHONPATH=$PWD/src uv run python -c "
from agents_af.retrieval_agent import RetrievalAgent
from agents_af.source_validator_agent import SourceValidatorAgent
from agents_af.reference_validator_agent import ReferenceValidatorAgent
from agents_af.answer_synthesis_agent import AnswerSynthesisAgent
from agents_af.sequential_orchestrator import SequentialWorkflowOrchestrator
from agent_framework import Agent, Executor, Message
from agent_framework.foundry import FoundryChatClient
from agent_framework.orchestrations import SequentialBuilder
print('✓ All Agent Framework imports OK')
"

# 2. Verify retrieval
echo ""
echo "=== Test 2: Agentic Retrieval ==="
PYTHONPATH=$PWD/src uv run python -c "
from agents_af.retrieval_agent import RetrievalAgent
agent = RetrievalAgent()
result = agent.run('What is the PTO policy?')
print(f'Matches: {len(result[\"matches\"])}')
print(f'Agentic: {bool(result.get(\"agentic_answer\"))}')
has_pto = any(m.get('policyNumber') == '51350' for m in result['matches'])
print('PASS' if has_pto else 'REVIEW')
"

# 3. Verify full pipeline
echo ""
echo "=== Test 3: Full AF Pipeline ==="
PYTHONPATH=$PWD/src uv run python -c "
from agents_af.sequential_orchestrator import SequentialWorkflowOrchestrator
orch = SequentialWorkflowOrchestrator()
result = orch.process_query('What is the holiday pay policy?')
print(f'Status: {result.get(\"status\")}')
print(f'Grounded: {result.get(\"is_grounded\")}')
print(f'Answer: {result.get(\"answer\", \"\")[:150]}')
has_holiday = 'holiday' in result.get('answer', '').lower() or '50715' in result.get('answer', '')
print('PASS' if result.get('is_grounded') and has_holiday else 'REVIEW')
"

# 4. Mock test suite
echo ""
echo "=== Test 4: Mock Test Suite ==="
PYTHONPATH=$PWD/src uv run python -m pytest tests/test_agents_af.py --mock -v

echo ""
echo "=== Done. Check Foundry Portal → Tracing for agent activity ==="
```
