# Bicep — HR Policy Knowledge Agent (Lab)

Resource-group-scoped Bicep module that deploys all lab resources.

## File Structure

| File | Description |
|------|-------------|
| [main.bicep](main.bicep) | All resource definitions, RBAC assignments, and outputs |
| [abbreviations.json](abbreviations.json) | Resource naming prefixes (e.g. `cog-`, `srch-`, `st`) |

The parent entry point is [`../main.bicep`](../main.bicep) (subscription scope), which creates the resource group and invokes this module.

## Deploying with `azd`

```bash
# From infra/ directory:
cd infra
azd auth login
azd up
```

If your azd environment is still set to `eastus2`, update it first. The lab deployment has been hitting capacity issues there.

```bash
azd env set AZURE_LOCATION eastus
```

## Deploying with Azure CLI

```bash
az deployment sub create \
  --location eastus \
  --template-file ../main.bicep \
  --parameters ../main.parameters.json \
  --parameters environmentName=lab location=eastus principalId=$(az ad signed-in-user show --query id -o tsv)
```

If Azure reports a global name conflict for Cognitive Services or Search, set `resourceNameSuffix` to a short unique value such as `lab2`.

## Parameters

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `environmentName` | string | _(required)_ | Environment name used as suffix in resource group and resource names |
| `location` | string | _(required)_ | Azure region for all resources |
| `resourcePrefix` | string | `hr-policy-kb-lab` | Prefix for resource naming |
| `resourceNameSuffix` | string | `""` | Optional override for globally unique resource names when a previous resource is stuck in a conflicting or soft-deleted state |
| `openAIDeploymentName` | string | `gpt-4.1` | GPT-4.1 deployment name |
| `gpt5DeploymentName` | string | `gpt-5` | GPT-5 deployment name |
| `embeddingDeploymentName` | string | `text-embedding-3-small` | Embedding model deployment name |
| `searchSku` | string | `basic` | AI Search SKU (`basic` or `standard`) |
| `principalId` | string | `""` | User/SP object ID for RBAC; leave empty to skip role assignments |
