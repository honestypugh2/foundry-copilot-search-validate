# Retrieval Patterns — Hybrid Search vs RAG vs Agentic Retrieval

> ⚠️ **Development reference only.** Production deployments must follow the
> [Azure Well-Architected Framework](https://learn.microsoft.com/azure/well-architected/)
> and Microsoft best practices. All three patterns below are implemented in
> this repo using **GA-only** Python packages (`azure-ai-projects` 2.2.0,
> `azure-search-documents` 12.0.0). The default Copilot Studio → Foundry Agent
> + MCP path uses **Agentic Retrieval (Pattern A)**.

This repo implements three retrieval styles against the same `hr_lab_index`
(integrated vectorization). All three are testable today on the GA stack.

| Pattern | Where | What it does | Calls to AI Search | LLM synthesis | Test entry point |
|---------|-------|--------------|--------------------|---------------|------------------|
| **Hybrid Search** | [`AzureAISearchClient.hybrid_search`](../src/search/azure_ai_search_client.py) | Single-shot text + vector + semantic ranker query; returns ranked chunks. No LLM. | 1 | none | [`tests/test_query_retrieval.py`](../tests/test_query_retrieval.py) |
| **RAG (retrieve + synthesize)** | Pattern B `file_metadata_lookup` → `AnswerSynthesisAgent` ([`src/agents/orchestrator_pattern_b.py`](../src/agents/orchestrator_pattern_b.py), [`src/agents/answer_synthesis_agent.py`](../src/agents/answer_synthesis_agent.py)) | Deterministic index query (or hybrid hits) → Foundry Agent synthesizes a grounded answer over the retrieved context. Classic RAG. | 1 | Foundry Agent (`gpt-4.1`) | [`tests/test_file_location_retrieval.py`](../tests/test_file_location_retrieval.py) |
| **Agentic Retrieval (default, Pattern A)** | [`AzureAISearchClient.agentic_retrieve`](../src/search/azure_ai_search_client.py) → `FoundryAgentOrchestrator` ([`src/agents/sequential_orchestrator_foundry.py`](../src/agents/sequential_orchestrator_foundry.py)) via `MCPTool` | Knowledge Base decomposes the conversation into subqueries, runs them in parallel, reranks, and (optionally) synthesizes an answer. Foundry Agent calls the KB through MCP with `tool_choice="required"`. | N (KB-managed) | KB internal model + Foundry Agent | [`tests/test_agentic_retrieval_extractive.py`](../tests/test_agentic_retrieval_extractive.py), [`tests/test_mcp_query_retrieval.py`](../tests/test_mcp_query_retrieval.py) |

## Switching patterns at runtime

```bash
# Pattern A — Agentic Retrieval via Foundry Agent + MCP (GA, default, recommended)
export ORCHESTRATOR_PATTERN=A
python main.py

# Pattern B — Hybrid MCP + deterministic metadata lookup (GA)
export ORCHESTRATOR_PATTERN=B
python main.py

# Hybrid Search only (no agent) — call the search client directly
python -c "from src.search.azure_ai_search_client import AzureAISearchClient; \
           import json; print(json.dumps(AzureAISearchClient().hybrid_search('PTO policy'), indent=2))"
```

The Knowledge Base output mode is configurable in `src/config/search_config.json`:

| `output_mode` | Behaviour |
|---------------|-----------|
| `ANSWER_SYNTHESIS` (default) | KB returns a synthesized answer + citations. |
| `EXTRACTIVE_DATA` | KB returns ranked chunks only; the Foundry Agent synthesizes. Lower latency, more control. |

In GA `azure-search-documents` 12.0.0 these map to the string literals
`"answerSynthesis"` and `"extractiveData"` (the `KnowledgeRetrievalOutputMode`
enum was removed). Reasoning effort tiers `low` / `medium` / `high` are
preview-only; the GA SDK exposes only `KnowledgeRetrievalMinimalReasoningEffort`,
and `azure_ai_search_client._resolve_reasoning_effort` warns and falls back to
`minimal` if a non-GA tier is configured. To experiment with the higher tiers,
use the preview environment under [`src/agents_af/`](../src/agents_af/README.md).

## When to use which

- **Hybrid Search** — you already have your own LLM / synthesis layer (e.g.
  Copilot Studio's built-in answer generation), and just need ranked chunks.
- **RAG** — you need deterministic control over retrieval (e.g. metadata-only
  questions like "where is policy X stored?") plus an LLM to phrase the answer.
- **Agentic Retrieval** — multi-turn conversational queries that benefit from
  query decomposition / subquery planning, with citations enforced by MCP.
  This is the supported default for the Copilot Studio integration.

## Comparison harness

The evaluation harness ([`evals/run_eval.py`](../evals/run_eval.py)) measures
the same query set across orchestrator patterns. Switch `ORCHESTRATOR_PATTERN`
between runs to compare end-to-end quality / latency / cost across A and B.
For Hybrid Search-only baselines, call `hybrid_search` directly from a script
and feed results into the same metrics module ([`evals/metrics.py`](../evals/metrics.py)).
