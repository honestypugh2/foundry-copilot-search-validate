# Infrastructure — HR Policy Knowledge Agent (Lab)

This directory contains Infrastructure as Code (IaC) for deploying the Azure **lab** environment, supporting `src/` code and `data/knowledge_base_lab/` documents.

| Option | Directory | Tool |
|--------|-----------|------|
| **Bicep** (recommended with `azd`) | [`bicep/`](bicep/) | Azure CLI / Azure Developer CLI |
| **Terraform** | [`terraform/`](terraform/) | Terraform CLI |

Both deploy the same set of resources into a single resource group.

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
| 1 | **Azure AI Foundry** (AIServices) + Project | Unified cognitive services account |
| 2 | **GPT-4.1** deployment | Chat / inference — Foundry agent model (GlobalStandard, 100 capacity) |
| 3 | **GPT-5** deployment | Advanced reasoning (GlobalStandard, 100 capacity) |
| 4 | **text-embedding-3-small** deployment | Vector embeddings for hybrid search (GlobalStandard, 120 capacity) |
| 5 | **Azure AI Search** | Hybrid search index with semantic ranker (`free` tier) |
| 6 | **Azure Document Intelligence** | Document parsing (prebuilt-layout, S0) |
| 7 | **Azure Storage Account** | Blob storage with `ask-hr-knowledge` container |
| 8 | **RBAC role assignments** | Least-privilege access for the lab user |

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

### Terraform

```bash
cd infra/terraform
cp terraform.tfvars.example terraform.tfvars   # fill in values
terraform init
terraform plan
terraform apply
```

## RBAC Roles Assigned

When `principalId` / `principal_id` is provided, these roles are granted at resource-group scope:

| Role | Purpose |
|------|---------|
| Azure AI User | Access AI Foundry project |
| Cognitive Services OpenAI User | Invoke OpenAI model deployments |
| Search Index Data Contributor | Read/write search index data |
| Search Service Contributor | Manage search service configuration |
| Storage Blob Data Contributor | Read/write blob data |

## Prerequisites

- Azure subscription with access to Azure OpenAI (GPT-4.1) and AI Search
- Azure CLI (`az`) logged in, or Terraform CLI with `azurerm` provider configured
- For Bicep: Azure Developer CLI (`azd`) recommended
- For Terraform: version >= 1.5.0
