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
                          │  → agents.orchestrator_factory      │
                          │     .get_orchestrator()             │
                          └──────────────┬──────────────┘
                                         │
                 ┌───────────────────────┴───────────────────────┐
                 │                                               │
    ┌────────────▼────────────┐         ┌────────────────────────▼────────────────────────┐
    │  Path 1: Agent Framework│         │     Path 2: Foundry Agent Service (default)      │
    │  + FoundryChatClient    │         │     agents/orchestrator_factory.py               │
    │  (agents_af/            │         │     selected via ORCHESTRATOR_PATTERN (A | B)    │
    │   sequential_           │         ├──────────────────────────┬──────────────────────┤
    │   orchestrator.py)      │         │  Pattern A (default)     │  Pattern B           │
    │                         │         │  sequential_orchestrator │  orchestrator_       │
    │  SequentialBuilder      │         │  _foundry.py             │  pattern_b.py        │
    │  Executors + Agent      │         │  Single-agent MCP        │  Hybrid MCP +        │
    │                         │         │                          │  metadata lookup     │
    └────────────┬────────────┘         └────────────┬─────────────────────────┬─────────┘
                 │                                    │                         │
                 └────────────────────────────────────┴─────────────────────────┘
                                         │
                          ┌──────────────▼──────────────┐
                          │  Azure AI Search             │
                          │  Agentic Retrieval (MCP)     │
                          │  hr_lab_index + knowledge base│
                          └──────────────────────────────┘
```

> **Note:** Path 1 (`agents_af/`) is the **preview Agent Framework** path,
> intended for testing only. Path 2 (`agents/`) is the **GA-only default**
> selected by the Azure Function via `orchestrator_factory.get_orchestrator()`,
> which reads the `ORCHESTRATOR_PATTERN` environment variable.

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
- `azure-ai-projects` 2.2.0 — AIProjectClient, PromptAgentDefinition, MCPTool, conversations, responses

### How it works

Uses the `azure-ai-projects` SDK directly to create and invoke Foundry
Agents via the conversations + responses API. Agents are registered as
PromptAgent definitions in the Foundry Portal and invoked at runtime.

The Azure Function selects the orchestrator through the factory, which reads
the `ORCHESTRATOR_PATTERN` environment variable (default `A`):

```python
from agents.orchestrator_factory import get_orchestrator
orchestrator = get_orchestrator()  # ORCHESTRATOR_PATTERN: A | B
```

| `ORCHESTRATOR_PATTERN` | Orchestrator | File | Behavior |
|------------------------|--------------|------|----------|
| `A` (default) | `FoundryAgentOrchestrator` | `sequential_orchestrator_foundry.py` | Single-agent MCP — one Foundry Agent + MCPTool with `tool_choice="required"` |
| `B` | `PatternBOrchestrator` | `orchestrator_pattern_b.py` | Hybrid — client-side classification routes file-location queries to a direct metadata lookup and content queries to the MCP agent |

Within Pattern A, the `PIPELINE_MODE` environment variable selects the
sub-mode: `single_agent` (default, MS recommended) or `multi_step` (legacy
4-step pipeline kept as a learning reference).

### Pattern A orchestrator: `sequential_orchestrator_foundry.py`

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

### Pattern A multi-step pipeline (`PIPELINE_MODE=multi_step`)

| Step | Agent | Type | Purpose |
|------|-------|------|---------|
| 1 | `RetrievalAgent` | Local (AzureAISearchClient) | Agentic retrieval / hybrid search |
| 2 | `SourceValidatorAgent` | Foundry Agent (GPT-4.1) | Deterministic filter + trust assessment |
| 3 | `ReferenceValidatorAgent` | Foundry Agent (GPT-4.1) | Citation extraction + grounding |
| 4 | `AnswerSynthesisAgent` | Foundry Agent (GPT-4.1) | Answer synthesis (or agentic passthrough) |

> The default `single_agent` mode skips steps 1–3; the single Foundry Agent +
> MCPTool performs retrieval, reasoning, and citation in one pass.

### Pattern B orchestrator: `orchestrator_pattern_b.py`

`PatternBOrchestrator` uses **client-side query classification** (regex) to
route each query to the optimal path:

- **Content questions** ("What does Policy 51350 say?") → `HRPolicyAgentB`
  Foundry Agent with MCPTool only (platform handles MCP natively, full citations)
- **File-location questions** ("Where is the PTO policy stored?") →
  `file_metadata_lookup` direct index search (bypasses MCP), then the no-tools
  `HRPolicyAgentB-FileLocation` agent formats the deterministic result

This yields **deterministic file paths** (no LLM hallucination of metadata
fields) while keeping native MCP citations for content queries. The Azure
Function also exposes a standalone `/api/lookup` endpoint that performs the
same metadata lookup with no Foundry/MCP/LLM dependency.

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
├── orchestrator_factory.py            # get_orchestrator() — reads ORCHESTRATOR_PATTERN
├── register_agents.py                 # One-time Foundry Agent registration script
├── retrieval_agent.py                 # Agentic retrieval / hybrid search
├── source_validator_agent.py          # Foundry Agent: source trust assessment
├── reference_validator_agent.py       # Foundry Agent: grounding quality
├── answer_synthesis_agent.py          # Foundry Agent: answer synthesis
├── sequential_orchestrator_foundry.py # Pattern A — single-agent MCP (default)
└── orchestrator_pattern_b.py          # Pattern B — hybrid MCP + metadata lookup
```

---

## Comparison

| Feature | Path 1 (Agent Framework) | Path 2 (Foundry Agent Service) |
|---------|-------------------------|-------------------------------|
| **SDK** | `agent-framework-foundry` | `azure-ai-projects` 2.2.0 |
| **LLM client** | `FoundryChatClient` | `AIProjectClient` → conversations/responses |
| **Orchestration** | `SequentialBuilder` (built-in) | Python `async/await` via `orchestrator_factory` (Pattern A / B) |
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
| **Azure Function** | `/api/ask`, `/api/lookup`, `/api/health` endpoints |
| **Copilot Studio** | `manifest.json` + `openapi.yaml` |
| **Config** | `src/config/search_config.json` |

---

## Switching Paths

### Default: Foundry Agent Service via the factory

The Azure Function (`function_app.py`) selects the orchestrator through the
factory, which honors the `ORCHESTRATOR_PATTERN` environment variable:

```python
# function_app.py
from agents.orchestrator_factory import get_orchestrator
orchestrator = get_orchestrator()  # ORCHESTRATOR_PATTERN: A (default) | B
```

```bash
# .env
ORCHESTRATOR_PATTERN=A   # Pattern A — single-agent MCP (default)
ORCHESTRATOR_PATTERN=B   # Pattern B — hybrid MCP + metadata lookup
```

### Switch to Agent Framework path

The Agent Framework path is not wired into the factory; import it directly
(testing only):

```python
from agents_af.sequential_orchestrator import SequentialWorkflowOrchestrator
```

### Configure Pattern A pipeline mode

Within Pattern A, use the `PIPELINE_MODE` environment variable:

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
PYTHONPATH=$PWD/src pytest tests/test_agents_af.py -v

# Foundry Agent Service agents
PYTHONPATH=$PWD/src pytest tests/test_agents_foundry.py -v

# Azure Function / Copilot Studio contract
PYTHONPATH=$PWD/src pytest tests/test_function_copilot.py -v
```

### End-to-end tests (live — requires Azure credentials, no mocks)

```bash
# Agent Framework path
PYTHONPATH=$PWD/src python tests/e2e_live_agent_framework.py

# Foundry Agent Service path
PYTHONPATH=$PWD/src python tests/e2e_live_foundry_service.py

# Full flow (original)
PYTHONPATH=$PWD/src python tests/test_full_flow.py
```

### Query retrieval tests

```bash
# 22 specific document lookups
PYTHONPATH=$PWD/src python tests/test_query_retrieval.py        # live
PYTHONPATH=$PWD/src python tests/test_query_retrieval.py --mock # mock

# MCP query retrieval — Pattern A / Pattern B
PYTHONPATH=$PWD/src python tests/test_mcp_query_retrieval.py --pattern A
PYTHONPATH=$PWD/src python tests/test_mcp_query_retrieval.py --pattern B
```
