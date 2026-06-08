# Infrastructure — HR Policy Knowledge Agent (Lab)

This directory contains the **Bicep** Infrastructure as Code (IaC) for deploying
the Azure **lab** environment, supporting `src/` code and
`data/knowledge_base_lab/` documents.

| Tool | Directory | Scope |
|------|-----------|-------|
| **Bicep** (with `azd` or Azure CLI) | [`bicep/`](bicep/) | Full stack (services, Function App, Key Vault, monitoring, networking, RBAC) |

> **Bicep is the single deployment path.** It deploys the complete environment
> end-to-end.

## Lab Environment Configuration

| Aspect | Value |
|--------|-------|
| Resource prefix | `hr-policy-kb-lab` |
| Recommended region | `eastus` |
| Chat model | GPT-4.1 |
| Blob container | `ask-hr-knowledge` |
| Code path | `src/` |
| Data path | `data/knowledge_base_lab/` |

## Resources Deployed

| # | Service | Purpose |
|---|---------|---------|
| 1 | **Azure AI Foundry** (AIServices) + Project | Unified cognitive services account + Foundry project |
| 2 | **GPT-4.1** deployment | Chat / inference — Foundry agent model (GlobalStandard, 100 capacity) |
| 3 | **GPT-5** deployment | Advanced reasoning (GlobalStandard, 100 capacity) |
| 4 | **text-embedding-3-small** deployment | Vector embeddings for hybrid search (GlobalStandard, 120 capacity) |
| 5 | **Azure AI Search** | Hybrid search index with semantic ranker (`standard` tier) |
| 6 | **Azure Document Intelligence** | Document parsing (prebuilt-layout, S0) |
| 7 | **Azure Storage Account** | Blob storage with `ask-hr-knowledge` container |
| 8 | **Azure Key Vault** | Centralized secrets (RBAC auth, purge protection) |
| 9 | **Log Analytics + Application Insights** | Telemetry; AI project ↔ App Insights connection for the Foundry Tracing tab |
| 10 | **Function App** (Flex Consumption, Python 3.13) | Hosts `/api/ask`, `/api/lookup`, `/api/health` with a user-assigned managed identity |
| 11 | **RBAC role assignments** | Least-privilege for the lab user, the Search managed identity, and the Function managed identity |
| 12 | **Networking** (optional, `enablePrivateEndpoints`) | VNet, private endpoints, private DNS zones, Function VNet integration |
| 13 | **Easy Auth v2** (optional, `enableFunctionAuth`) | Entra ID bearer-token validation for the Copilot Studio AAD flow |

## Entry Points

### Bicep (`azd up`)

The subscription-level entry point is [main.bicep](main.bicep), which creates the resource group and delegates to [bicep/main.bicep](bicep/main.bicep). Parameters are supplied via [main.parameters.json](main.parameters.json).

From the repo root, change into this directory and deploy:

```bash
cd infra
azd auth login
azd up
```

If `eastus2` is configured in your azd environment, switch it before retrying. The lab stack uses Azure AI Foundry model deployments and has been hitting capacity failures in `eastus2`.

```bash
azd env set AZURE_LOCATION eastus
azd up
```

If Azure reports `A resource with this name already exists or is in a conflicting state`, redeploy with a short suffix to rotate the globally unique Cognitive Services and Search names:

```bash
az deployment sub create \
  --location eastus \
  --template-file infra/main.bicep \
  --parameters infra/main.parameters.json \
  --parameters environmentName=lab location=eastus resourceNameSuffix=lab2 principalId=$(az ad signed-in-user show --query id -o tsv)
```

## RBAC Roles Assigned

**Lab user principal** (when `principalId` is provided, at resource-group scope):

| Role | Purpose |
|------|---------|
| Azure AI User | Access AI Foundry project |
| Cognitive Services OpenAI User | Invoke OpenAI model deployments |
| Search Index Data Contributor | Read/write search index data |
| Search Service Contributor | Manage search service configuration |
| Storage Blob Data Contributor | Read/write blob data |
| Key Vault Secrets Officer | Manage secrets locally |

**Search service managed identity:**

| Role | Scope | Purpose |
|------|-------|---------|
| Storage Blob Data Reader | Data storage | Indexer pulls blobs |
| Cognitive Services OpenAI User | AI Services | Skillset calls the embedding model |

**Function App managed identity:**

| Role | Scope | Purpose |
|------|-------|---------|
| Storage Blob Data Owner | Function runtime storage | `AzureWebJobsStorage` via managed identity |
| Storage Blob Data Reader | Data storage | Read knowledge-base blobs |
| Search Index Data Reader | Search | Query-time index access (`/api/ask`, `/api/lookup`) |
| Cognitive Services OpenAI User | AI Services | Embeddings + chat inference |
| Azure AI User | AI Services | Foundry project access |
| Key Vault Secrets User | Key Vault | Read secrets |

## Prerequisites

- Azure subscription with access to Azure OpenAI (GPT-4.1) and AI Search
- Azure CLI (`az`) logged in
- Azure Developer CLI (`azd`) recommended
