# Eval harness

Automated quality scoring for the HR Policy Knowledge Lab pipeline.

## Dataset

[dataset.py](dataset.py) defines **88 cases** across 8 categories:

| Category | Description |
|---|---|
| `lookup` | Exact policy-number lookups (Find Policy 51350…) |
| `topic` | Title / topic queries (What is the PTO policy?) |
| `paraphrase` | Same intent, different wording (Vacation time policy?) |
| `disambiguation` | Near-duplicates that must not be confused (full-time vs part-time PTO) |
| `location` | Where-is queries (faithfulness scoring disabled — answer is a path) |
| `multi_intent` | Content + location combined |
| `multi_doc` | Recall hit if ANY of the expected files is returned |
| `adversarial` | Out-of-scope / prompt injection — should refuse |

## Metrics

[metrics.py](metrics.py) computes:

1. **Retrieval recall@1/@3/@5 + MRR** — does an expected file appear in the
   top-k matches/references returned by the orchestrator?
2. **Citation accuracy** — extracts MCP citations (`【msg:search†source】`) from
   the answer and verifies they reference an expected file.
3. **Refusal detection** — on adversarial cases, did the agent decline?
4. **Faithfulness (LLM-as-judge)** — uses an Azure OpenAI deployment to score
   whether every factual claim in the answer is supported by the retrieved
   context. Returns 0.0–1.0 with a one-sentence rationale.

## Run

```bash
# Full dataset (live — needs Foundry + Search RBAC)
uv run python -m evals.run_eval

# Offline retrieval-only smoke test (skip the judge LLM call)
uv run python -m evals.run_eval --skip-faithfulness --concurrency 4

# Filter by category or case ID
uv run python -m evals.run_eval --category paraphrase,disambiguation
uv run python -m evals.run_eval --case-id T01,L05,A05

# Limit for quick iteration
uv run python -m evals.run_eval --limit 5
```

Reports land in `evals/results/eval_<UTC-timestamp>.{json,md}`.

## CI integration

Add a nightly GitHub Action that runs `python -m evals.run_eval
--skip-faithfulness` against a dev environment and uploads the markdown
report as a job artifact. Gate releases on `recall@5 >= 0.90` and
`citation_accuracy >= 0.85`.
