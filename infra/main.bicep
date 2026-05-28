// ============================================================================
// HR Policy Knowledge Agent - Lab Infrastructure
// Subscription-level entry point (creates resource group + delegates to module)
// Supports src_lab/ code and data/knowledge_base_lab/ documents
// ============================================================================
targetScope = 'subscription'

@minLength(1)
@maxLength(64)
@description('Name of the azd environment (used as resource prefix)')
param environmentName string

@description('Azure region for all resources')
param location string

@description('Resource prefix for naming')
param resourcePrefix string = 'hr-policy-kb-lab'

@description('Optional suffix to rotate globally unique resource names when Azure leaves a resource in a conflicting or soft-deleted state')
param resourceNameSuffix string = ''

@description('Azure OpenAI chat model deployment name')
param openAIDeploymentName string = 'gpt-4.1'

@description('Azure OpenAI GPT-5 deployment name')
param gpt5DeploymentName string = 'gpt-5'

@description('Azure OpenAI embedding model deployment name')
param embeddingDeploymentName string = 'text-embedding-3-small'

@description('Azure AI Search SKU')
@allowed(['basic', 'standard'])
param searchSku string = 'basic'

@description('Semantic ranker tier for Azure AI Search')
@allowed(['free', 'standard', 'disabled'])
param semanticSearchTier string = 'standard'

@description('Enable VNet + Private Endpoints for production network isolation')
param enablePrivateEndpoints bool = false

@description('Disable local (API key) auth on AI Services and Search')
param disableLocalAuth bool = false

@description('Enable Entra ID (Easy Auth v2) on the Function App for Copilot Studio AAD flow')
param enableFunctionAuth bool = false

@description('Entra ID application (client) ID for the Function App registration')
param functionAuthClientId string = ''

@description('Additional client app IDs allowed to call the Function (e.g. Copilot Studio app ID)')
param functionAuthAllowedClientIds array = []

@description('Principal ID for RBAC role assignments (e.g. your user or service principal objectId)')
param principalId string = ''

// ---------- Resource Group ----------
resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: 'rg-${resourcePrefix}-${environmentName}'
  location: location
}

// ---------- Deploy all lab resources into the resource group ----------
module resources './bicep/main.bicep' = {
  name: take('resources-${environmentName}', 64)
  scope: rg
  params: {
    environmentName: environmentName
    location: location
    resourcePrefix: resourcePrefix
    resourceNameSuffix: resourceNameSuffix
    openAIDeploymentName: openAIDeploymentName
    gpt5DeploymentName: gpt5DeploymentName
    embeddingDeploymentName: embeddingDeploymentName
    searchSku: searchSku
    semanticSearchTier: semanticSearchTier
    enablePrivateEndpoints: enablePrivateEndpoints
    disableLocalAuth: disableLocalAuth
    enableFunctionAuth: enableFunctionAuth
    functionAuthClientId: functionAuthClientId
    functionAuthAllowedClientIds: functionAuthAllowedClientIds
    principalId: principalId
  }
}

// ---------- Outputs (surfaced to azd) ----------
output AZURE_RESOURCE_GROUP string = rg.name
output AZURE_OPENAI_ENDPOINT string = resources.outputs.openAIEndpoint
output AZURE_OPENAI_DEPLOYMENT string = resources.outputs.openAIDeploymentName
output AZURE_GPT5_DEPLOYMENT string = resources.outputs.gpt5DeploymentName
output AZURE_OPENAI_EMBEDDING_DEPLOYMENT string = resources.outputs.embeddingDeploymentName
output AZURE_AI_FOUNDRY_RESOURCE string = resources.outputs.aiFoundryResourceName
output AZURE_AI_PROJECT_NAME string = resources.outputs.aiProjectName
output AZURE_AI_PROJECT_ENDPOINT string = resources.outputs.projectEndpoint
output AZURE_SEARCH_ENDPOINT string = resources.outputs.searchEndpoint
output AZURE_SEARCH_NAME string = resources.outputs.searchName
output AZURE_DOCUMENT_INTELLIGENCE_ENDPOINT string = resources.outputs.docIntelligenceEndpoint
output AZURE_STORAGE_ACCOUNT string = resources.outputs.storageAccountName
output AZURE_FUNCTION_APP_NAME string = resources.outputs.functionAppName
output AZURE_FUNCTION_ENDPOINT string = resources.outputs.functionAppEndpoint
output AZURE_KEY_VAULT_NAME string = resources.outputs.keyVaultName
output AZURE_KEY_VAULT_URI string = resources.outputs.keyVaultUri
output APPLICATIONINSIGHTS_CONNECTION_STRING string = resources.outputs.appInsightsConnectionString
