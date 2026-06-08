# Copilot Studio Routing — Dual-Tool Setup (No Foundry)

This guide wires the HR policy agent in **Copilot Studio** so it routes by itself,
**without any Foundry dependency**. Two destinations:

| Destination | Used for | Latency | How it's added |
| --- | --- | --- | --- |
| **Azure AI Search knowledge source** | Content questions — what a policy *says*, eligibility, amounts, deadlines, process | ~10–14s (RAG + model synthesis) | Knowledge source on the agent |
| **`lookupHRPolicyDocument` REST tool** (`POST /api/lookup`) | Location questions — *where* a document is stored, file path, blob URL, link, filename | ~1–2s (single hybrid search, metadata only) | Custom REST tool from `copilot/openapi-lookup-v2.json` |

> Because Foundry is dropped, **the routing decision now lives entirely in Copilot Studio**
> (agent instructions + tool descriptions). There is no server-side orchestrator picking
> the path — Copilot Studio's planner does it.

---

## 1. Add the lookup tool

1. In Copilot Studio open your agent → **Tools** → **Add a tool** → **New tool** → **REST API**.
2. Upload / paste **`copilot/openapi-lookup-v2.json`** (Swagger 2.0).
3. **Authentication:** API key.
   - Parameter name: `code`
   - **Location: Query** (Header returns **401**).
   - Value: the function key —
     `az functionapp keys list -g rg-hr-policy-kb-lab-dev -n func-hr-policy-kb-lab-dev --query "functionKeys.default" -o tsv`
4. Save. Confirm the operation `lookupHRPolicyDocument` appears.

Keep the existing **Azure AI Search knowledge source** on the agent for content.

---

## 2. Agent instructions (Lever 1 — the router)

Paste into the agent's **Instructions**. This is what makes Copilot Studio pick the right path.

```text
You are an HR policy assistant. Decide how to answer each question:

1. LOCATION questions — when the user asks WHERE a document is, or asks for a
   file path, storage path, blob URL, link, download link, or filename
   (e.g. "Where is the PTO policy stored?", "What's the file path for the
   Holiday Pay policy?", "Give me the link to the Code of Ethics document"):
   → Call the lookupHRPolicyDocument tool. Return the blob_url / file path and
     the filename exactly as the tool provides them. Do not summarize content.

2. CONTENT questions — when the user asks what a policy SAYS, how it works,
   eligibility, amounts, deadlines, or process (e.g. "How much PTO do I accrue?",
   "Who is eligible for holiday pay?"):
   → Answer from the HR policy knowledge source with citations. Do NOT call the
     lookupHRPolicyDocument tool.

3. If a question asks for BOTH the content and where to find it, answer the
   content from the knowledge source first, then call lookupHRPolicyDocument to
   append the document link.

Never invent file paths, URLs, or policy numbers. If the lookup tool returns
found = false, say you couldn't locate that document and ask the user to
clarify the policy name or number.
```

---

## 3. Tool description (Lever 2 — reinforce the boundary)

The `description` fields in `openapi-lookup-v2.json` already steer the planner:

- **Operation description** explicitly says *"Use this tool ONLY when the user asks WHERE a
  document is located… Do NOT use this tool for questions about what a policy SAYS."*

If you maintain a content tool (`askHRPolicy` from `copilot/openapi-v2.json`) instead of a
knowledge source, keep its description in the mirror-image form: *"Use for what a policy says…
do NOT use for where a document is stored."* Clear, non-overlapping descriptions are the
strongest signal the planner has.

---

## 4. Verify routing (Test pane)

| # | Test prompt | Expected route | What correct looks like |
| --- | --- | --- | --- |
| 1 | Where is the PTO policy stored? | `lookupHRPolicyDocument` | blob_url + filename, ~1–2s, no prose summary |
| 2 | What's the file path for the Holiday Pay policy? | `lookupHRPolicyDocument` | metadata_storage_path returned verbatim |
| 3 | Give me the link to the Code of Ethics document | `lookupHRPolicyDocument` | blob_url for policy 31000 |
| 4 | How much PTO do I accrue per year? | Knowledge source | cited answer from PTO policy 51350, no tool call |
| 5 | Who is eligible for holiday pay? | Knowledge source | cited answer, no tool call |
| 6 | Tell me about the Code of Ethics and where I can find it | Knowledge source **then** tool | content answer + appended link |
| 7 | Where can I download the Blood Borne Pathogens intro? | `lookupHRPolicyDocument` | blob_url for policy 101100 |

If a content question (4/5) wrongly calls the lookup tool, tighten instruction rule #2 and
re-confirm the tool's operation description hasn't been edited to sound generic.

---

## 5. Why this is fast

`POST /api/lookup` runs **one** `hybrid_search` and returns metadata fields only — no Foundry
agent, no MCP, no LLM synthesis. That's the ~1–2s vs. ~10–14s difference. Content questions
still go through the knowledge source's RAG + synthesis, which is where the latency lives.

See also: [`docs/CopilotStudioHybridExample.md`](CopilotStudioHybridExample.md) for the full
click-by-click build, and [`copilot/openapi-lookup-v2.json`](../copilot/openapi-lookup-v2.json)
for the tool spec.
