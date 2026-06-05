# Agent Architecture: Two Paths

This document describes the two agent orchestration paths available in the
HR Policy Knowledge Base Lab. Both paths share the same Azure AI Search
infrastructure (index, skillset, agentic retrieval) and the same Azure
Function endpoint (`/api/ask`) consumed by Copilot Studio.

---

## Architecture Overview

```
                          ┌─────────────────────────────┐
                          │     Copilot Studio / Teams   │
                          └──────────────┬──────────────┘
                                         │ POST /api/ask
                          ┌──────────────▼──────────────┐
                          │  Azure Function (function_app.py)   │
                          └──────────────┬──────────────┘
                                         │
                 ┌───────────────────────┼───────────────────────┐
                 │                       │                       │
    ┌────────────▼────────────┐  ┌───────▼────────┐  ┌─────────▼──────────┐
    │  Path 1: Agent Framework│  │  Path 2: Foundry│  │ Path 2b: Foundry   │
    │  + FoundryChatClient    │  │  Agent Service  │  │  Agent Service     │
    │  (agents_af/)           │  │  (agents/       │  │  (agents/          │
    │                         │  │   sequential_   │  │   sequential_      │
    │  SequentialBuilder      │  │   orchestrator  │  │   orchestrator_    │
    │  Executors + Agent      │  │   .py)          │  │   foundry.py)      │
    └─────────────────────────┘  └────────────────┘  └────────────────────┘
                 │                       │                       │
                 └───────────────────────┼───────────────────────┘
                                         │
                          ┌──────────────▼──────────────┐
                          │  Azure AI Search             │
                          │  Agentic Retrieval           │
                          │  (KnowledgeBaseRetrievalClient)│
                          └──────────────────────────────┘
```

---

## Path 1: Agent Framework + FoundryChatClient

**Directory:** `src/agents_af/`

**SDK packages:**
- `agent-framework` — core Agent, Executor, Message, WorkflowContext
- `agent-framework-foundry` — FoundryChatClient (Foundry Responses endpoint)
- `agent-framework-orchestrations` — SequentialBuilder

### How it works

Uses Microsoft Agent Framework's `SequentialBuilder` to chain custom
`Executor`s (deterministic logic) with a `FoundryChatClient`-backed `Agent`
(LLM inference). The FoundryChatClient connects to a Foundry project
endpoint and uses the Responses API for model inference.

```python
from agent_framework import Agent, Executor, Message, WorkflowContext, handler
from agent_framework.foundry import FoundryChatClient
from agent_framework.orchestrations import SequentialBuilder

# FoundryChatClient for model inference
chat_client = FoundryChatClient(
    project_endpoint=os.environ["FOUNDRY_PROJECT_ENDPOINT"],
    model="gpt-4.1",
    credential=DefaultAzureCredential(),
)

# Create an Agent backed by FoundryChatClient
synthesis_agent = chat_client.as_agent(
    instructions="You are an HR policy assistant...",
    name="hr_answer_synthesis",
)

# Build the sequential pipeline
workflow = SequentialBuilder(
    participants=[
        RetrievalExecutor(),          # custom Executor — agentic retrieval
        SourceValidationExecutor(),   # custom Executor — deterministic filter
        ReferenceValidationExecutor(),# custom Executor — deterministic extract
        AnswerSynthesisExecutor(),    # custom Executor — routes to Agent or passthrough
        synthesis_agent,              # FoundryChatClient Agent — LLM synthesis
        FinalSynthesisExecutor(),     # custom Executor — captures final output
    ]
).build()
```

### Pipeline steps

| Step | Participant | Type | Purpose |
|------|-------------|------|---------|
| 1 | `RetrievalExecutor` | Custom Executor | Agentic retrieval / hybrid search |
| 2 | `SourceValidationExecutor` | Custom Executor | Filter to trusted `ask-hr-knowledge` container |
| 3 | `ReferenceValidationExecutor` | Custom Executor | Extract citations + check grounding |
| 4 | `AnswerSynthesisExecutor` | Custom Executor | Route: agentic answer → passthrough, else → Agent |
| 5 | `hr_answer_synthesis` | FoundryChatClient Agent | GPT-4.1 answer synthesis (only if needed) |
| 6 | `FinalSynthesisExecutor` | Custom Executor | Capture Agent output + yield final results |

### Key characteristics

- **App owns the agent definition** — instructions, tools, and session handling are in your code
- **SequentialBuilder** handles the orchestration lifecycle
- **Custom Executors** process deterministic steps without LLM calls
- **Full conversation history** flows between participants automatically
- When agentic retrieval provides a pre-synthesized answer, Step 5 (LLM) is effectively skipped

### Environment variables

```bash
FOUNDRY_PROJECT_ENDPOINT=https://<your-project>.services.ai.azure.com
FOUNDRY_MODEL=gpt-4.1
AZURE_SEARCH_ENDPOINT=https://<search>.search.windows.net
AZURE_SEARCH_INDEX_NAME=hr_lab_index
```

### Files

```
src/agents_af/
├── __init__.py
├── retrieval_agent.py           # Agentic retrieval / hybrid search
├── source_validator_agent.py    # Deterministic container filter
├── reference_validator_agent.py # Deterministic citation extraction
├── answer_synthesis_agent.py    # Context builder + agentic passthrough
└── sequential_orchestrator.py   # SequentialBuilder orchestrator
```

---

## Path 2: Foundry Agent Service (azure-ai-projects SDK)

**Directory:** `src/agents/`

**SDK packages:**
- `azure-ai-projects` (≥2.0.0) — AIProjectClient, PromptAgentDefinition, MCPTool, conversations, responses

### How it works

Uses the `azure-ai-projects` SDK directly to create and invoke Foundry
Agents via the conversations + responses API. Agents are registered as
PromptAgent definitions in the Foundry Portal and invoked at runtime.

The **default mode** (`PIPELINE_MODE=single_agent`) uses a single Foundry Agent
with an MCPTool pointing to the Azure AI Search knowledge base MCP endpoint.
`tool_choice="required"` ensures the agent always retrieves from the knowledge base.

An optional **multi-step mode** (`PIPELINE_MODE=multi_step`) runs a 4-step pipeline
using Python async/await orchestration (no SequentialBuilder).

### Orchestrator: `sequential_orchestrator_foundry.py`

Coordinates the pipeline directly with Python `async/await` using
`AIProjectClient` → MCPTool + `tool_choice="required"`.
No Agent Framework SequentialBuilder — the orchestration is in Python code.

```python
from azure.ai.projects.aio import AIProjectClient
from azure.ai.projects.models import PromptAgentDefinition

from agents.foundry_client import _ensure_foundry_agent

async with AIProjectClient(endpoint=endpoint, credential=credential) as client:
    # Get-or-create: reuse the existing version when one exists, only mint a
    # new version when the agent is missing or RECREATE_FOUNDRY_AGENTS=true.
    # This is what stops the Foundry portal from prompting "Save the Agent"
    # between runs.
    agent = await _ensure_foundry_agent(
        client,
        agent_name="HRAnswerSynthesisFoundry",
        definition=PromptAgentDefinition(model="gpt-4.1", instructions="..."),
        role_label="Pattern A single-agent MCP",
    )
    openai_client = client.get_openai_client()
    conversation = await openai_client.conversations.create()
    stream = await openai_client.responses.create(
        conversation=conversation.id,
        extra_body={"agent_reference": {"name": agent.name, "type": "agent_reference"}},
        input=prompt,
        stream=True,
    )
```

### Pipeline steps (both sub-paths)

| Step | Agent | Type | Purpose |
|------|-------|------|---------|
| 1 | `RetrievalAgent` | Local (AzureAISearchClient) | Agentic retrieval / hybrid search |
| 2 | `SourceValidatorAgent` | Foundry Agent (GPT-4.1) | Deterministic filter + trust assessment |
| 3 | `ReferenceValidatorAgent` | Foundry Agent (GPT-4.1) | Citation extraction + grounding |
| 4 | `AnswerSynthesisAgent` | Foundry Agent (GPT-4.1) | Answer synthesis (or agentic passthrough) |

### Key characteristics

- **Agents persist in Foundry Portal** — registered once, invoked by name at runtime
- **Foundry-managed conversations** — conversation lifecycle handled by the service
- **Shared `foundry_client.py`** provides singleton `AIProjectClient` + `OpenAI` client
- **Deterministic + LLM hybrid** — each agent class has a deterministic path that always runs, with optional Foundry Agent enhancement
- **Graceful fallback** — if the Foundry Agent is unavailable, agents use deterministic-only mode

### Environment variables

```bash
AZURE_AI_PROJECT_ENDPOINT=https://<project>.services.ai.azure.com/api/projects/<project>
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4.1
AZURE_SEARCH_ENDPOINT=https://<search>.search.windows.net
AZURE_SEARCH_INDEX_NAME=hr_lab_index
```

### Files

```
src/agents/
├── __init__.py
├── foundry_client.py                  # Shared AIProjectClient + OpenAI helpers
├── register_agents.py                 # One-time Foundry Agent registration script
├── retrieval_agent.py                 # Agentic retrieval / hybrid search
├── source_validator_agent.py          # Foundry Agent: source trust assessment
├── reference_validator_agent.py       # Foundry Agent: grounding quality
├── answer_synthesis_agent.py          # Foundry Agent: answer synthesis
└── sequential_orchestrator_foundry.py # Foundry-native orchestrator (single-agent MCP default)
```

---

## Comparison

| Feature | Path 1 (Agent Framework) | Path 2 (Foundry Agent Service) |
|---------|-------------------------|-------------------------------|
| **SDK** | `agent-framework-foundry` | `azure-ai-projects` (≥2.0.0) |
| **LLM client** | `FoundryChatClient` | `AIProjectClient` → conversations/responses |
| **Orchestration** | `SequentialBuilder` (built-in) | SequentialBuilder **or** Python async |
| **Agent definition** | App-owned (in code) | Service-managed (Foundry Portal) |
| **Agent persistence** | Ephemeral per workflow | Persistent in Foundry Portal |
| **Deterministic steps** | Custom `Executor`s | Python functions |
| **Streaming** | Built-in via SequentialBuilder events | Manual `async for event in stream` |
| **Conversation history** | Automatic (AF manages it) | Manual (`conversations.create/delete`) |
| **Best for** | Full control, AF ecosystem | Foundry Portal integration, shared agents |

---

## Shared Infrastructure

Both paths share:

| Component | Resource |
|-----------|----------|
| **Azure AI Search** | `hr_lab_index` with HNSW + semantic ranker |
| **Agentic Retrieval** | `hr-knowledge-source` + `hr-knowledge-base` |
| **Azure OpenAI** | GPT-4.1 deployment |
| **Azure Blob Storage** | `ask-hr-knowledge` container |
| **Azure Function** | `/api/ask` endpoint |
| **Copilot Studio** | `manifest.json` + `openapi.yaml` |
| **Config** | `src/config/search_config.json` |

---

## Switching Paths

### Default: FoundryAgentOrchestrator (single-agent MCP)

The Azure Function (`function_app.py`) defaults to the Foundry orchestrator:

```python
from agents.sequential_orchestrator_foundry import FoundryAgentOrchestrator
```

### Switch to Agent Framework path

```python
from agents_af.sequential_orchestrator import SequentialWorkflowOrchestrator
```

### Configure pipeline mode

Use the `PIPELINE_MODE` environment variable:

```python
# .env
PIPELINE_MODE=single_agent   # MS recommended (default)
PIPELINE_MODE=multi_step     # Legacy 4-step pipeline (learning reference)
```

---

## Testing

### Unit tests (no Azure required)

```bash
# Agent Framework agents
PYTHONPATH=$PWD/src pytest src/tests/test_agents_af.py -v

# Foundry Agent Service agents
PYTHONPATH=$PWD/src pytest src/tests/test_agents_foundry.py -v

# Azure Function / Copilot Studio contract
PYTHONPATH=$PWD/src pytest src/tests/test_function_copilot.py -v
```

### End-to-end tests (requires Azure credentials)

```bash
# Agent Framework path
PYTHONPATH=$PWD/src python src/tests/test_e2e_agent_framework.py

# Foundry Agent Service path
PYTHONPATH=$PWD/src python src/tests/test_e2e_foundry_service.py

# Full flow (original)
PYTHONPATH=$PWD/src python src/tests/test_full_flow.py

# Mock mode (no Azure)
PYTHONPATH=$PWD/src python src/tests/test_e2e_agent_framework.py --mock
PYTHONPATH=$PWD/src python src/tests/test_e2e_foundry_service.py --mock
```

### Query retrieval tests (22 specific document lookups)

```bash
PYTHONPATH=$PWD/src python src/tests/test_query_retrieval.py        # live
PYTHONPATH=$PWD/src python src/tests/test_query_retrieval.py --mock # mock
```
