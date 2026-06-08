# Preview: Microsoft Agent Framework + Agentic Retrieval (TESTING ONLY)

> ⚠️ **DEVELOPMENT / EXPERIMENTATION ONLY.** This path uses preview (rc / b) Python
> packages and is **not** the supported production pattern for this repo.
> The default Copilot Studio → Foundry Agent + MCP path under
> [`src/agents/`](../agents/) uses **GA-only** packages and follows the
> Microsoft-recommended single-agent MCP pattern. Production deployments must
> follow [Azure Well-Architected Framework](https://learn.microsoft.com/azure/well-architected/)
> and Microsoft best practices — preview packages do not carry SLA, support, or
> backwards-compatibility guarantees.

## What lives here

`src/agents_af/` contains an alternative orchestration that uses the
[Microsoft Agent Framework](https://learn.microsoft.com/agent-framework/) with
`agent-framework-foundry` (`FoundryChatClient`) and `agent-framework-orchestrations`
(`SequentialBuilder`) to coordinate a multi-step pipeline:

```
RetrievalAgent → SourceValidatorAgent → ReferenceValidatorAgent → AnswerSynthesisAgent
```

It exists so we can compare the Agent Framework programming model against the
GA `azure-ai-projects` Foundry Agent + MCP path and to test agentic retrieval
features that are still in preview (e.g. low / medium / high reasoning effort
tiers in `azure-search-documents` 11.7.0b2).

## Why preview-only and isolated

`agent-framework==1.7` transitively pins `azure-search-documents==11.7.0b2`
through `agent-framework-azure-ai-search`. That preview SDK is **incompatible**
with the GA `azure-search-documents>=12.0.0` used by the default path:

| Surface                              | Preview 11.7.0b2 | GA 12.0.0 |
|--------------------------------------|------------------|-----------|
| `KnowledgeBaseRetrievalClient`       | ✅               | ✅ |
| `KnowledgeBase`, `SearchIndexKnowledgeSource` | ✅       | ✅ |
| `KnowledgeRetrievalMinimalReasoningEffort` | ✅          | ✅ |
| `KnowledgeRetrievalLowReasoningEffort`     | ✅          | ❌ removed |
| `KnowledgeRetrievalMediumReasoningEffort`  | ✅          | ❌ removed |
| `KnowledgeRetrievalOutputMode` (enum)      | ✅          | ❌ removed (use string `"answerSynthesis"` / `"extractiveData"`) |

Because of this conflict the preview deps are **not** part of the default
`pyproject.toml` resolution and **must** be installed in a separate virtual
environment.

## Install the preview environment

```bash
# 1. Create a dedicated venv (do NOT reuse the default GA env).
uv venv .venv-agents-af --python 3.13
source .venv-agents-af/bin/activate

# 2. Install the preview pin set with prereleases explicitly allowed.
uv pip install -r requirements-agents-af.txt --prerelease=allow

# (pip equivalent)
# python -m venv .venv-agents-af
# source .venv-agents-af/bin/activate
# pip install --pre -r requirements-agents-af.txt
```

The pinned versions are:

| Package                              | Version       |
|--------------------------------------|---------------|
| `agent-framework`                    | `1.7.0`       |
| `agent-framework-foundry`            | `1.7.0`       |
| `agent-framework-orchestrations`     | `1.0.0rc2`    |
| `agent-framework-azure-ai`           | `1.0.0rc6`    |
| `azure-ai-inference`                 | `1.0.0b9`     |
| `azure-search-documents`             | `11.7.0b2`    |

## Run the preview orchestrator

The `src/agents/orchestrator_factory.py` factory dispatches by
`ORCHESTRATOR_PATTERN`. Add the Agent Framework branch when testing locally:

```bash
export ORCHESTRATOR_PATTERN=AF   # or whatever value you wire into the factory
python main.py
```

The Agent Framework orchestrator entry point is
[`src/agents_af/sequential_orchestrator.py`](sequential_orchestrator.py).

## Production guidance

Do not deploy `src/agents_af/` to production. The supported, GA-only deployment
artefacts are:

- `src/agents/sequential_orchestrator_foundry.py` (Pattern A — default)
- `src/agents/orchestrator_pattern_b.py` (Pattern B — hybrid MCP + metadata)
- `src/api/function_app.py` (Azure Functions host)
- `infra/` Bicep

These rely only on `azure-ai-projects` 2.2.0 GA and `azure-search-documents`
12.0.0 GA. See the top-level [README](../../README.md) for the production
architecture and the [WAF checklist](https://learn.microsoft.com/azure/well-architected/)
before promoting any code from this folder.
