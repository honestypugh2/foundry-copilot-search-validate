# Working Example — Copilot Studio Hybrid Orchestration (HR Policy)

A complete, click-by-click walkthrough that builds the **hybrid orchestration
model** described in
[CopilotStudioIntegration.md → Orchestration Model](CopilotStudioIntegration.md#orchestration-model--copilot-studio-decides-when-to-call-foundry-hybrid).

In this example, **Copilot Studio decides per turn** whether to:

1. answer directly (greeting / clarifying question),
2. answer from an uploaded glossary
   ([copilot/quick_reference_guide.md](../copilot/quick_reference_guide.md)), or
3. call the **`askHRPolicy`** Foundry tool (which runs Pattern A or B inside the
   Azure Function).

The backend is unchanged — `ORCHESTRATOR_PATTERN` (A/B) still controls what
happens inside `/api/ask`. This example uses **Pattern B** so file-location
queries return deterministic blob paths.

---

## Prerequisites

| Requirement | Value for this example |
|---|---|
| Function App base URL | `https://func-hr-policy-kb-lab-dev.azurewebsites.net` |
| Endpoint | `POST /api/ask` (FUNCTION auth), `GET /api/health` (anonymous) |
| Backend pattern | `ORCHESTRATOR_PATTERN=B` (set on the Function App) |
| OpenAPI spec | [copilot/openapi-v2.json](../copilot/openapi-v2.json) (Swagger 2.0 JSON) |
| Glossary | [copilot/quick_reference_guide.md](../copilot/quick_reference_guide.md) |
| Same tenant | Copilot Studio environment and the Azure subscription share one Entra tenant |

Confirm the backend is live before you start:

```bash
curl -sS https://func-hr-policy-kb-lab-dev.azurewebsites.net/api/health
# -> {"status":"ok"}
```

Retrieve the function key (you paste it at connection time, Step 6 — never put
it in the spec):

```bash
az functionapp keys list -g rg-hr-policy-kb-lab-dev \
  -n func-hr-policy-kb-lab-dev --query "functionKeys.default" -o tsv
```

---

## Step 1 — Create the agent

1. Go to [Copilot Studio](https://copilotstudio.microsoft.com) → confirm the
   correct **environment** (top-right).
2. **Create** → **New agent**.
3. **Name**: `Ask HR Policy Assistant`
4. **Description**: `Answers employee HR policy questions from the ASK HR knowledge base, with grounded citations.`
5. **Language**: English → **Create**.

## Step 2 — Paste the routing instructions (Lever 1)

On **Overview → Instructions**, paste the Lever 1 routing instructions from
[CopilotStudioIntegration.md → Lever 1](CopilotStudioIntegration.md#lever-1--routing-instructions-paste-into-agent-instructions),
then **Save**. These rules tell orchestration when to answer directly, when to
use the glossary, and when to call `askHRPolicy`.

## Step 3 — Turn off general knowledge (grounded-only)

1. **Settings → Generative AI**.
2. **Use generative AI orchestration** = **Yes** (required — this is what makes
   per-turn routing work).
3. Turn **off** *Allow the AI to use its own general knowledge*.
4. **Content moderation** = **High** → **Save**.

> Without generative orchestration, Copilot Studio can't choose between
> answering directly and calling the tool — the hybrid model depends on it.

## Step 4 — Upload the glossary (enables the direct-answer path)

1. **Knowledge → Add knowledge → Upload file**.
2. Upload [copilot/quick_reference_guide.md](../copilot/quick_reference_guide.md).
3. **Add** → wait for **Ready**.

This is what lets "What does PTO stand for?" and "What's the Holiday Pay policy
number?" be answered **without** a Foundry call.

## Step 5 — Add the `askHRPolicy` REST API tool (Lever 2)

1. **Tools → Add a tool → New tool → REST API**.
2. **Upload specification**: [copilot/openapi-v2.json](../copilot/openapi-v2.json)
   (Swagger 2.0 JSON — do **not** upload the v3 YAML; the v3→v2 auto-translate
   can drop the body schema and auth).
3. Confirm the `askHRPolicy` operation is detected. Its **description** is the
   tightened Lever 2 text ("use this tool when… do NOT use this for…") — this is
   the strongest routing signal. **Next**.
4. **Authentication**:

   | Field | Value | Notes |
   |---|---|---|
   | Authentication | **API key** | Matches `securityDefinitions.functionKey` |
   | Parameter label | `Function key` | Cosmetic |
   | Parameter name | `code` | The query param the Function expects |
   | Parameter location | **Query** | ⚠️ Change from the default **Header** — Header → 401 |

   **Next**. (You do **not** paste the key here — only at Step 6.)
5. **Select tools** → check `askHRPolicy` → **Next**.
6. **Configure tool + parameters** → confirm `query` (input) and the outputs
   (`answer`, `status`, `pattern`, `pipeline_mode`, `is_grounded`, …) all have
   descriptions → **Next**.

## Step 6 — Publish and connect

1. **Review → Publish** → wait for *publish complete*.
2. **Create connection** → paste the function key from the
   `az functionapp keys list` command above → **Create**.
3. **Add and configure** to attach the tool.

## Step 7 — Configure tool behavior

1. On the tool page → **Details** → check
   **Allow agent to decide dynamically when to use the tool** (this is what lets
   orchestration call it only when the routing rules match).
2. **Completion** → **Write the response with generative AI** (so citations are
   surfaced) → **Save**.

---

## Step 8 — Test the routing in the Test pane

Open the **Test** pane and run these in order. Watch whether the
**`askHRPolicy`** tool fires (the Test pane shows the tool call + the `query`
sent).

| # | Prompt | Expected | Tool called? |
|---|---|---|---|
| 1 | `Hi, I need HR help` | Greets, asks which policy area | ❌ No |
| 2 | `What does PTO stand for?` | "Paid Time Off" (from glossary) | ❌ No |
| 3 | `What's the policy number for Holiday Pay?` | "50715" (from glossary) | ❌ No |
| 4 | `What is the PTO policy?` | Grounded answer, cites Policy 51350 | ✅ Yes |
| 5 | `Where is the PTO policy document stored?` | Blob path/filename (Pattern B) | ✅ Yes |
| 6 | `Summarize PTO and tell me where it's stored` | Answer + source in one reply | ✅ Yes (once) |
| 7 | `What is the capital of France?` | Declines — HR policy only | ❌ No |

**Pass criteria**: rows 1–3 and 7 do **not** call the tool; rows 4–6 do, and
rows 4–6 return grounded text with citation markers
(`【message_idx:search_idx†source_name】`).

### What "correct" looks like (row 4)

```text
User: What is the PTO policy?
askHRPolicy  ← tool call, query="What is the PTO policy?"
Assistant: Full-time employees accrue Paid Time Off (PTO) under Policy 51350 …
           【4:0†51350 - Types of Leave_ Paid Time Off (PTO).docx】
```

### What "correct" looks like (row 5, Pattern B)

```text
User: Where is the PTO policy document stored?
askHRPolicy  ← tool call
Assistant: The PTO policy (51350) is stored at:
  https://stovcrqidayerac.blob.core.windows.net/ask-hr-knowledge/
  knowledge_base_lab/51350%20-%20Types%20of%20Leave_%20Paid%20Time%20Off%20(PTO).docx
```

---

## Tuning the routing

| Symptom | Fix |
|---|---|
| Tool fires on greetings / acronyms (**over-called**) | Strengthen the *"do NOT use"* clause in the Lever 2 description; add the missing term to the glossary |
| Agent answers from general knowledge instead of calling the tool (**under-called**) | Add synonyms to the *"use when"* clause; re-confirm general knowledge is **OFF** (Step 3) |
| `401 Unauthorized` | Auth **Parameter location** must be **Query**, name `code` (Step 5.4) |
| Generic answers, no citations | **Completion** = *Write the response with generative AI* (Step 7) |
| `503` on first call | Flex Consumption cold start — `az functionapp restart -g rg-hr-policy-kb-lab-dev -n func-hr-policy-kb-lab-dev`, retry |

> The single most effective lever is the **tool description** (Lever 2). If
> routing is wrong, tighten it before touching the agent instructions.

---

## How this maps to the backend patterns

| Layer | Decision | Where |
|---|---|---|
| Copilot Studio (this example) | answer directly / glossary / call tool | Instructions + tool description |
| `/api/ask` Function | which orchestrator runs | `ORCHESTRATOR_PATTERN` (A or B) |
| Pattern B inside the Function | content vs. file-location | Server-side classifier in [orchestrator_pattern_b.py](../src/agents/orchestrator_pattern_b.py) |

The Copilot layer never re-implements the Pattern B content/file-location
split — it only decides **whether** to call `askHRPolicy`. That keeps `/api/ask`
as the single retrieval brain and avoids duplicate routing logic.

## References

- [Extend your agent with tools from a REST API (preview)](https://learn.microsoft.com/en-us/microsoft-copilot-studio/agent-extend-action-rest-api)
- [Add a Foundry agent to Copilot Studio](https://learn.microsoft.com/en-us/microsoft-copilot-studio/add-agent-foundry-agent)
- [CopilotStudioIntegration.md](CopilotStudioIntegration.md)
- [CopilotStudioAuth.md](CopilotStudioAuth.md)
