# Cost Comparison — HR Policy Copilot Patterns

Cost breakdown and comparison across the integration paths and orchestration
patterns in this project. Figures are anchored to the **Azure Intake & Case
Management Cost Model v2** spreadsheet (customer **Novarta MDR**, region
**US East**), verified against the Azure Pricing Calculator (May 2025).

> **Two cost layers — keep them separate.** The cost-model spreadsheet prices
> the **Azure AI Search infrastructure layer** (the index, semantic ranker,
> agentic-retrieval tokens, enrichment, embeddings, storage). That layer is a
> **fixed/shared floor paid by every path** (Path 1, Pattern A, Pattern B,
> hybrid) — it does not change with orchestration choice. The **per-message
> layer** (Copilot Studio messages + Azure Functions executions + Foundry
> synthesis tokens) sits **on top** and *is* where the patterns differ; the
> spreadsheet does not price it, so those cells use scenario inputs.

---

## Measured baseline — Azure AI Search layer (from the cost model)

Production inputs (Novarta MDR):

| Input | Value |
|---|---|
| Region | US East |
| Documents indexed | 2,700,000 |
| Queries / month | 50,000 |
| Semantic queries % | 60% (≈30,000 semantic queries) |
| Agentic tokens / month | 150,000,000 |
| Incremental indexing | +5,000 new docs/mo × 3 pages = 135,000 pages |
| SKU / tier | **S1**, Partitions = 2, Replicas = 2 → **4 Search Units** |
| Rate per SU | $245.28 |

Production monthly cost (sum of line items):

| Category | Service | SKU/Tier | Driver | Monthly (USD) | Annual (USD) |
|---|---|---|---|--:|--:|
| Azure AI Search | Search Service | S1 | 4 SU (2P × 2R) | **$981.12** | $11,773.44 |
| AI Features | Semantic Ranker | Standard | ~30K semantic queries (1K free) | $29.00 | $348.00 |
| AI Features | Agentic Retrieval | Standard | 150M tokens (50M free) | $3.20 | $38.40 |
| Enrichment | ContentUnderstanding | Standard | 135,000 new pages/mo | $1.35 | $16.20 |
| Related Services | Azure OpenAI Embeddings | text-embedding-3-small | 66K chunks × 800 tok = 52.8M tok | $0.06 | $12.67 |
| Related Services | Azure Blob Storage | Hot | ~10 GB source docs | $0.18 | $2.16 |
| **TOTAL (Production)** | | | | **$1,014.91** | **$13,178.07** |

> The **Search Service tier itself is ~97% of the AI Search-layer cost**
> ($981.12 of $1,014.91). Semantic ranker, agentic retrieval, enrichment,
> embeddings, and storage are rounding error by comparison. This is the **fixed
> floor every pattern pays**, whether or not the Foundry backend is called.

### Non-prod add-on and all-up

| Item | Monthly (USD) | Annual (USD) |
|---|--:|--:|
| Production | $1,014.91 | $13,178.07 |
| Non-prod add-on (2 envs × 25% of prod) | $507.45 | $6,089.44 |
| **All-up incl. non-prod** | **$1,522.36** | **$18,268.31** |

---

## Scope and assumptions (per-message layer)

These inputs drive the **per-message layer only** — they are *additional* to the
AI Search floor above.

| Assumption | Value (edit to match your scenario) |
|---|---|
| Region | US East |
| Monthly active employees | «scenario» (e.g., 5,000) |
| Sessions / employee / month | «scenario» (e.g., 4) |
| Turns (messages) / session | «scenario» (e.g., 5) |
| **Total monthly messages** | sessions × turns (e.g., 100,000) |
| Currency | USD |
| Pricing basis | Pay-as-you-go list price |

The **single biggest per-message lever** is *what fraction of messages reach the
backend* (`/api/ask` → Azure Functions + Foundry synthesis tokens). The hybrid
orchestration model exists to drive that fraction **down**. The AI Search floor
is unchanged either way.

---

## Cost components (the line items)

| # | Component | Billing unit | Driver | Per-pattern relevance |
|---|---|---|---|---|
| 1 | **Copilot Studio messages** | per message (capacity pack / PAYG) | every user turn | All paths (fixed front door) |
| 2 | **Azure Functions (Flex Consumption)** | per execution + GB-seconds | each `/api/ask` call | Path 2 (A, B, hybrid) |
| 3 | **Azure AI Foundry — model inference** | per 1M input/output tokens | each backend retrieval + synthesis | Path 2 (A, B, hybrid) |
| 4 | **Azure AI Search** | tier × hours (fixed) + semantic ranker units | index always on | Path 1 and Path 2 |
| 5 | **Azure Blob Storage** | GB-month + transactions | document store | All (negligible) |
| 6 | **Application Insights** | per GB ingested | telemetry volume | All (optional) |

### Rate card

AI Search-layer rates are from the cost model; per-message-layer rates are
scenario inputs (fill from the Pricing Calculator for your agreement).

| Component | Unit | List price | Source |
|---|---|---|---|
| AI Search — S1 Search Unit | per SU/month | **$245.28** | cost model |
| Semantic ranker | per 1K queries over 1K free | **$1.00** | cost model |
| Agentic retrieval | per 1M tokens over 50M free | **$0.032** | cost model |
| ContentUnderstanding (Minimal) | per 1K pages | **$0.01** | cost model |
| Embeddings (text-embedding-3-small) | per 1K tokens | **$0.00002** | cost model |
| Blob Storage (hot) | per GB-month | **$0.018** | cost model |
| Copilot Studio message | per message | «scenario» | Pricing Calculator |
| Functions Flex — executions | per 1M executions | «scenario» | Pricing Calculator |
| Functions Flex — compute | per GB-second | «scenario» | Pricing Calculator |
| Foundry model — input tokens | per 1M tokens | «scenario» | Pricing Calculator |
| Foundry model — output tokens | per 1M tokens | «scenario» | Pricing Calculator |

---

## SKU tier comparison (AI Search scalability)

From the cost model — the Search tier is the dominant lever, so right-sizing it
matters most:

| SKU | Rate / SU | 2 SU | 4 SU (2P×2R) | 12 SU (3P×4R) | Notes |
|---|--:|--:|--:|--:|---|
| Basic | $73.73 | $147.46 | n/a (max 3 SU) | n/a | Small workloads, limited features |
| **S1** | $245.28 | $490.56 | **$981.12** | $2,943.36 | Standard production — **recommended** |
| S2 | $981.13 | $1,962.24 | $3,924.52 | — | Medium–large workloads |
| S3 | $1,907.47 | $3,814.94 | — | — | Large enterprise workloads |
| L1 / L2 | — | — | — | — | Storage-optimized (1 TB / 2 TB per partition) |

**HA note:** the 4 SU (2P×2R) config buys high availability via 2 replicas.
Dropping to 1 replica (2 SU) ≈ **saves $490/month** if HA isn't required.

---

## Per-pattern cost profile

### Path 1 — Azure AI Search as Knowledge Source (no backend)

Copilot Studio queries `hr_lab_index` directly; its built-in LLM synthesizes
the answer. **No Functions, no Foundry tokens.**

| Component | Incurred? | Notes |
|---|---|---|
| Copilot Studio messages | ✅ every turn | Built-in LLM synthesis is included in the message |
| Azure Functions | ❌ | Not used |
| Foundry model tokens | ❌ | Not used |
| AI Search | ✅ fixed tier + per-query semantic ranker | |
| Blob / App Insights | ✅ minimal | |

**Lowest variable cost**, but no agentic retrieval (no query planning, subquery
decomposition, server-side grounding, or deterministic file paths).

### Path 2 / Pattern A — Single-Agent MCP

Every backend turn = 1 Function execution + 1 Foundry agent run (query planning
→ subqueries → semantic ranking → synthesis).

| Component | Incurred per backend turn |
|---|---|
| Copilot Studio message | 1 |
| Function execution | 1 |
| Foundry tokens | 1 retrieval+synthesis pass (multiple subqueries → **higher** token count) |
| AI Search | fixed + semantic ranker queries |

**Highest token cost per backend call** because MCP plans and runs multiple
subqueries. Best answer quality for "what/how" questions.

### Path 2 / Pattern B — Hybrid MCP + Metadata Lookup

Same as A for content questions. **File-location questions skip the model** —
the server-side classifier routes them to a deterministic metadata lookup
(near-zero token cost).

| Question type | Function | Foundry tokens |
|---|---|---|
| Content ("what is the PTO policy?") | 1 | full MCP pass (= Pattern A) |
| File-location ("where is it stored?") | 1 | **~0** (deterministic lookup) |

**Cheaper than A** in proportion to your file-location query mix. Same
Copilot Studio message cost.

### Copilot-orchestrated **Hybrid** (this project's new model)

Copilot Studio decides per turn whether to hit the backend at all. Greetings,
acronym lookups, and policy-number lookups are answered **client-side** (message
cost only — **no Function, no Foundry tokens**).

| Turn type | Copilot msg | Function | Foundry tokens |
|---|---|---|---|
| Greeting / clarify | ✅ | ❌ | ❌ |
| Glossary (acronym, policy #) | ✅ | ❌ | ❌ |
| Content question | ✅ | ✅ | full pass |
| File-location question | ✅ | ✅ | ~0 (Pattern B) |
| Off-topic | ✅ | ❌ | ❌ |

**Lowest backend cost of the Path 2 options.** It does not reduce Copilot
Studio message cost (that's per turn regardless) — it removes Functions
executions and Foundry tokens for the non-retrieval share of traffic.

---

## Side-by-side comparison

| Dimension | Path 1 (Direct) | Pattern A | Pattern B | Copilot Hybrid (over B) |
|---|---|---|---|---|
| Copilot Studio messages | per turn | per turn | per turn | per turn |
| Function executions | none | every backend turn | every backend turn | **only retrieval turns** |
| Foundry tokens | none | every turn (multi-subquery) | content turns only | **content turns only** |
| File-location cost | n/a (not supported) | full model pass | ~0 (deterministic) | ~0 (deterministic) |
| Greeting / glossary cost | message only | message + full backend | message + full backend | **message only** |
| Answer quality | good | best (agentic) | best + deterministic paths | best + deterministic paths |
| Relative total variable cost | **lowest** | **highest** | medium | **lowest of Path 2** |

> **Net:** Copilot-orchestrated Hybrid on top of Pattern B gives you the
> agentic-retrieval quality of Path 2 while pushing per-message backend cost
> toward Path 1 for the non-retrieval share of traffic.

---

## Worked example (plug in your rate card)

Assume **100,000 messages/month** and a traffic mix:

| Turn type | Share | Messages |
|---|---|---|
| Greeting / clarify | 20% | 20,000 |
| Glossary lookup | 15% | 15,000 |
| Content question | 50% | 50,000 |
| File-location question | 10% | 10,000 |
| Off-topic | 5% | 5,000 |

**Backend calls (`/api/ask`) by pattern:**

| Pattern | Backend calls | Full-model passes (tokens) |
|---|---|---|
| Pattern A | 100,000 (all turns) | 100,000 |
| Pattern B | 100,000 (all turns) | 90,000 (file-location ~0) |
| **Copilot Hybrid** | **60,000** (content + file-location) | **50,000** |

So vs. Pattern A, the hybrid model eliminates **40,000 Function executions** and
**50,000 full model passes** per month — the greeting/glossary/off-topic 40%.

**Monthly cost:**

The **AI Search floor ($1,014.91/mo) is identical for every pattern** — it does
not move with the orchestration choice. Only the per-message layer below differs.

| Line | Pattern A | Pattern B | Copilot Hybrid |
|---|---|---|---|
| **AI Search floor (fixed)** | **$1,014.91** | **$1,014.91** | **$1,014.91** |
| Copilot Studio messages | 100,000 × «msg rate» | 100,000 × «msg rate» | 100,000 × «msg rate» |
| Function executions | 100,000 × «exec rate» | 100,000 × «exec rate» | 60,000 × «exec rate» |
| Function GB-seconds | «calls × avg GB-s × rate» | «…» | «60% of A» |
| Foundry input tokens | 100,000 × «avg in» × «in rate» | 90,000 × … | 50,000 × … |
| Foundry output tokens | 100,000 × «avg out» × «out rate» | 90,000 × … | 50,000 × … |
| **Total / month** | **floor + highest** | **floor + medium** | **floor + lowest** |

> Fill the «…» per-message cells from the
> [Azure Pricing Calculator](https://azure.microsoft.com/pricing/calculator/).
> The **AI Search floor and the call-volume columns are already exact** — the
> hybrid model's saving is entirely in the per-message layer (40K fewer Function
> executions, 50K fewer model passes vs. Pattern A). The $1,014.91 floor is
> unaffected.

---

## Non-prod sensitivity (from the cost model)

Total cost scales with how many non-prod environments you run and their size
relative to production ($1,014.91/mo prod baseline):

| Scenario | Envs | % of prod | Non-prod add-on | All-up monthly | All-up annual |
|---|--:|--:|--:|--:|--:|
| Minimal (Dev only) | 1 | 25% | $253.73 | $1,268.63 | $15,223.59 |
| **Lean (Dev + Test) — current** | **2** | **25%** | **$507.45** | **$1,522.36** | **$18,268.31** |
| Standard (Dev/ST/UAT) | 3 | 35% | $761.18 | $1,776.09 | $21,313.09 |
| Enterprise (4 envs) | 4 | 35% | $1,014.91 | $2,029.82 | $24,357.74 |
| Full SDLC (5 envs) | 5 | 25% | $1,268.63 | $2,283.54 | $27,402.46 |

> Dropping to a **single Dev-only** non-prod env saves ~**$254/month** vs. the
> current Lean (Dev + Test) configuration.

---

## One-time re-index cost (full re-index)

A full re-index of all 2,700,000 documents is a **one-time** charge, not
ongoing:

| Service | Calculation | One-time (USD) |
|---|---|--:|
| ContentUnderstanding — content extraction | 2.7M docs × 3 pages = 8.1M pages (Minimal tier) | $81.00 |
| Embeddings (text-embedding-3-small) | 52.8M tokens re-embedded | $1.06 |
| Image extraction / contextualization | disabled | $0.00 |
| **Total one-time re-index** | | **~$82.06** |

---

## Cost-optimization levers (highest impact first)

1. **Right-size the AI Search tier / replicas (biggest lever).** The S1 4 SU
   config is **$981/mo — 97% of the AI Search floor**. If HA isn't required,
   1 replica (2 SU) ≈ **−$490/month**. Validate storage needs before any S2
   upgrade (S1 supports 75 GB/partition).
2. **Trim non-prod environments.** Dev-only vs. Dev + Test ≈ **−$254/month**.
3. **Increase the client-side share** — more glossary entries
   ([copilot/quick_reference_guide.md](../copilot/quick_reference_guide.md)) and
   a tighter Lever 2 tool description push more turns off the backend
   (per-message layer).
4. **Use Pattern B, not A** — deterministic file-location lookups remove model
   passes for "where is it stored?" traffic.
5. **Batch agentic-retrieval queries** — the cost model notes this could cut
   agentic-retrieval token cost 30–40% (small absolute — currently $3.20/mo).
6. **Keep text-embedding-3-small** — ~10× cheaper than ada-002 and sufficient
   for this RAG workload.
7. **Right-size the Foundry synthesis model** (per-message layer) — a smaller
   deployment cuts token cost where quality allows.
8. **Flex Consumption scale-to-zero** — keep `alwaysReady` at 0 unless cold
   starts hurt UX.

---

## Why production costs ~100× the test/POC

The customer observed ~$5–$10 in a Free/Basic test environment; this model
projects **$1,014.91/mo** production (≈100×), driven by:

| Factor | Test | Production |
|---|---|---|
| Search Units | 1 SU | 4 SU (HA + capacity) |
| SKU tier | Free / Basic | S1 |
| Semantic queries | within free tier | ~29K billable (60% of 30K) |
| Availability | hours/days | 24/7/365 continuous |

---

## How to finalize this doc

1. The **AI Search-layer** figures are populated from the cost model (Novarta
   MDR, S1 4 SU). Update them if the SKU, SU count, or volumes change.
2. Fill the **per-message-layer** «scenario» rates from the
   [Azure Pricing Calculator](https://azure.microsoft.com/pricing/calculator/)
   for Copilot Studio messages, Functions, and Foundry tokens.
3. The **worked-example call-volume columns** are pre-computed — multiply by the
   per-message rates to get dollar deltas.
4. Keep the **traffic mix** updated from Copilot Studio analytics; the
   client-side share is what makes the hybrid model cheaper in the per-message
   layer (the AI Search floor is unaffected).

## References

- [Azure Pricing Calculator](https://azure.microsoft.com/pricing/calculator/)
- [Copilot Studio licensing & messages](https://learn.microsoft.com/en-us/microsoft-copilot-studio/requirements-messages-management)
- [Azure Functions Flex Consumption pricing](https://azure.microsoft.com/pricing/details/functions/)
- [Azure AI Search pricing](https://azure.microsoft.com/pricing/details/search/)
- [Azure AI Foundry / model pricing](https://azure.microsoft.com/pricing/details/ai-foundry/)
- [CopilotStudioIntegration.md](CopilotStudioIntegration.md) ·
  [CopilotStudioHybridExample.md](CopilotStudioHybridExample.md)
