# MergeSkill Recommendations

## Status

MergeSkill is **disabled by default** in the indexing pipeline.  It is retained
as an optional fallback and can be enabled via:

```bash
# Environment variable
USE_MERGE_SKILL=true PYTHONPATH=$PWD/src uv run python src/scripts/index_knowledge_base.py --reset

# CLI flag
PYTHONPATH=$PWD/src uv run python src/scripts/index_knowledge_base.py --merge-skill --reset
```

## Why MergeSkill Was Removed from the Default Pipeline

### Problem

The goal was to prepend `metadata_storage_path` (the blob URL) to each text
chunk at index time so that every child document stores a composite
`snippet_with_source` field of the form:

```
https://storage.blob.core.windows.net/.../Policy.docx | <chunk text>
```

### Root Cause — StringCollection Type Constraint

MergeSkill's `itemsToInsert` input requires a **StringCollection** (an array
of strings).  When the skill context is set to `/document/text_sections/*`
(per-chunk), every field reference resolves to a single scalar value, not an
array.  Azure AI Search cannot auto-wrap a scalar into a single-element array.

Attempts to work around this included:

| Approach | Result |
|----------|--------|
| `source` set to the chunk path | Warning: *"Optional skill input was not of the expected type 'StringCollection'"* — silently dropped |
| `sourceContext` + nested `inputs` | Error: *"Input 'itemsToInsert' cannot specify sourceContext or inputs with source defined"* |
| `sourceContext` without `source` | Accepted at creation, but `itemsToInsert` resolves to an empty array at runtime — `snippet_with_source` is always `null` |

There is no SDK-supported way to construct a single-element StringCollection
from a scalar field without introducing a **WebApiSkill** (custom HTTP
endpoint) or a **ShaperSkill** pipeline that reshapes the data.

### Why These Alternatives Were Not Pursued

- **WebApiSkill**: Requires deploying and maintaining an Azure Function or
  API endpoint solely to wrap a string into `["string"]`.  Adds latency,
  cost, and operational complexity for a trivial transformation.
- **ShaperSkill**: Can produce complex objects but cannot convert a scalar
  `String` into a `StringCollection` — only rearranges existing shapes.

## Recommended Approach — Query-Time Construction

Since `blob_url` and `snippet` are **both populated on every child
document** by the index projections, `snippet_with_source` is constructed at
query time in `AzureAISearchClient.hybrid_search()`:

```python
source = result.get("snippet_with_source", "") or ""
if not source and blob_url and content:
    source = f"{blob_url} | {content}"
```

This approach:

- Eliminates skillset warnings and null-field issues
- Reduces skillset complexity (two skills instead of three)
- Removes a runtime dependency on MergeSkill's type system
- Costs nothing — no extra API calls or enrichment charges
- Falls back gracefully to index-time data if MergeSkill is ever enabled

## Pipeline Architecture

### Default (MergeSkill disabled)

```
ContentUnderstandingSkill → AzureOpenAIEmbeddingSkill
        ↓                           ↓
  text_sections/*             text_sections/*/text_vector
        ↓                           ↓
  Index Projections → hr_lab_index (child docs)
        ↓
  Query-time: blob_url + snippet → source
```

### With MergeSkill enabled (`--merge-skill`)

```
ContentUnderstandingSkill → MergeSkill → AzureOpenAIEmbeddingSkill
        ↓                      ↓                ↓
  text_sections/*    snippet_with_source    text_vector
        ↓                      ↓                ↓
  Index Projections → hr_lab_index (child docs)
```

## ContentUnderstandingSkill vs SplitSkill

The pipeline previously used `SplitSkill` for chunking.  It now uses
`ContentUnderstandingSkill`, which provides:

| Feature | SplitSkill | ContentUnderstandingSkill |
|---------|-----------|--------------------------|
| Content extraction | Requires indexer `dataToExtract: contentAndMetadata` | Reads raw `file_data` directly |
| Output format | Plain text chunks | Markdown-formatted chunks (tables, headers preserved) |
| Cross-page tables | Split at page boundary | Recognized as single unit |
| Image extraction | Not supported | Supported via `extractionOptions` |
| Location metadata | Not available | Page numbers and visual positions |
| Billing | Free (built-in) | Charged per Content Understanding API call |
| Page limit | None | 300 pages max per document |

### Requirements for ContentUnderstandingSkill

1. **Indexer**: `allowSkillsetToReadFileData: true` in indexer parameters
2. **Skillset**: `cognitive_services_account` pointing to the Foundry AI
   Services resource via `AIServicesAccountIdentity`
3. **RBAC**: Search service managed identity needs **Cognitive Services User**
   role on the AI Services resource
4. **URL**: Use `https://<resource>.services.ai.azure.com` (not
   `.cognitiveservices.azure.com`) as the `subdomain_url`
