// ============================================================================
// HR Policy Knowledge Agent - Lab Resources (Resource Group scope)
// Deploys the services needed for the lab environment:
//   1. Azure AI Foundry (AIServices) + Project
//   2. GPT-4.1 deployment (chat/inference — Foundry agent model)
//   3. GPT-5 deployment (advanced reasoning)
//   4. text-embedding-3-small deployment (vector embeddings)
//   5. Azure AI Search (semantic ranker enabled)
//   6. Azure Document Intelligence
//   7. Azure Storage Account + ask-hr-knowledge blob container
//   8. RBAC role assignments
//
// Supports: src_lab/ code, data/knowledge_base_lab/ documents
// ============================================================================

@description('Name of the azd environment')
param environmentName string

@description('Azure region for all resources')
param location string

@description('Resource prefix for naming')
param resourcePrefix string

@description('Optional suffix to rotate globally unique resource names when Azure leaves a resource in a conflicting or soft-deleted state')
param resourceNameSuffix string = ''

@description('Azure OpenAI chat model deployment name')
param openAIDeploymentName string

@description('Azure OpenAI GPT-5 deployment name')
param gpt5DeploymentName string

@description('Azure OpenAI embedding model deployment name')
param embeddingDeploymentName string

@description('Azure AI Search SKU')
param searchSku string

@description('Semantic ranker tier for Azure AI Search')
@allowed(['free', 'standard', 'disabled'])
param semanticSearchTier string = 'standard'

@description('Enable VNet + Private Endpoints for production network isolation')
param enablePrivateEndpoints bool = false

@description('Disable local (API key) auth on AI Services — recommended for production')
param disableLocalAuth bool = false

@description('Enable Entra ID (Easy Auth v2) on the Function App for Copilot Studio AAD flow')
param enableFunctionAuth bool = false

@description('Entra ID application (client) ID for the Function App registration')
param functionAuthClientId string = ''

@description('Entra ID tenant ID for the Function App auth (defaults to subscription tenant)')
param functionAuthTenantId string = subscription().tenantId

@description('Additional client app IDs allowed to call the Function (e.g. Copilot Studio app ID)')
param functionAuthAllowedClientIds array = []

@description('Principal ID for RBAC role assignments')
param principalId string

// ---------- Naming ----------
var abbrs = loadJsonContent('./abbreviations.json')
var defaultUniqueSuffix = uniqueString(resourceGroup().id)
var uniqueSuffix = empty(resourceNameSuffix) ? defaultUniqueSuffix : toLower(resourceNameSuffix)
var storageAccountPrefix = 'st'
var resourceToken = toLower('${resourcePrefix}-${environmentName}-${uniqueSuffix}')

// ============================================================================
// 1. Azure AI Foundry (AIServices) — unified cognitive services account
// ============================================================================
resource aiServices 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: '${abbrs.cognitiveServicesAccounts}${resourceToken}'
  location: location
  kind: 'AIServices'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: '${abbrs.cognitiveServicesAccounts}${resourceToken}'
    publicNetworkAccess: enablePrivateEndpoints ? 'Disabled' : 'Enabled'
    allowProjectManagement: true
    disableLocalAuth: disableLocalAuth
  }
}

// ============================================================================
// 2. AI Foundry Project (child of AIServices)
// ============================================================================
resource aiProject 'Microsoft.CognitiveServices/accounts/projects@2025-04-01-preview' = {
  parent: aiServices
  name: '${abbrs.cognitiveServicesProjects}${resourceToken}'
  location: location
  identity: { type: 'SystemAssigned' }
  properties: {
    description: 'HR Policy Knowledge Agent - Lab Project'
  }
}

// ============================================================================
// 3. GPT-4.1 Deployment (chat / inference — Foundry agent model)
// ============================================================================
resource gpt41Deployment 'Microsoft.CognitiveServices/accounts/deployments@2025-04-01-preview' = {
  parent: aiServices
  name: openAIDeploymentName
  sku: {
    name: 'GlobalStandard'
    capacity: 100
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-4.1'
      version: '2025-04-14'
    }
  }
}

// ============================================================================
// 4. GPT-5 Deployment (advanced reasoning)
// ============================================================================
resource gpt5Deployment 'Microsoft.CognitiveServices/accounts/deployments@2025-04-01-preview' = {
  parent: aiServices
  name: gpt5DeploymentName
  sku: {
    name: 'GlobalStandard'
    capacity: 100
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'gpt-5'
      version: '2025-08-07'
    }
  }
  dependsOn: [gpt41Deployment]
}

// ============================================================================
// 5. text-embedding-3-small Deployment (vector embeddings for hybrid search)
// ============================================================================
resource embeddingDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-04-01-preview' = {
  parent: aiServices
  name: embeddingDeploymentName
  sku: {
    name: 'GlobalStandard'
    capacity: 120
  }
  properties: {
    model: {
      format: 'OpenAI'
      name: 'text-embedding-3-small'
      version: '1'
    }
  }
  dependsOn: [gpt5Deployment]
}

// ============================================================================
// 6. Azure AI Search (semantic ranker enabled for hybrid search)
// ============================================================================
resource search 'Microsoft.Search/searchServices@2024-06-01-preview' = {
  name: '${abbrs.searchSearchServices}${resourceToken}'
  location: location
  sku: { name: searchSku }
  identity: { type: 'SystemAssigned' }
  properties: {
    replicaCount: 1
    partitionCount: 1
    hostingMode: 'default'
    semanticSearch: semanticSearchTier
    publicNetworkAccess: enablePrivateEndpoints ? 'disabled' : 'enabled'
    disableLocalAuth: disableLocalAuth
  }
}

// ============================================================================
// 7. Azure Document Intelligence (FormRecognizer)
// ============================================================================
resource docIntelligence 'Microsoft.CognitiveServices/accounts@2025-04-01-preview' = {
  name: '${abbrs.cognitiveServicesFormRecognizer}${resourceToken}'
  location: location
  kind: 'FormRecognizer'
  sku: { name: 'S0' }
  identity: { type: 'SystemAssigned' }
  properties: {
    customSubDomainName: '${abbrs.cognitiveServicesFormRecognizer}${resourceToken}'
    publicNetworkAccess: enablePrivateEndpoints ? 'Disabled' : 'Enabled'
    disableLocalAuth: disableLocalAuth
  }
}

// ============================================================================
// 8. Azure Storage Account (knowledge base documents + blob storage)
// ============================================================================
resource storage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: '${storageAccountPrefix}${defaultUniqueSuffix}'
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowSharedKeyAccess: !disableLocalAuth
    publicNetworkAccess: enablePrivateEndpoints ? 'Disabled' : 'Enabled'
    networkAcls: {
      defaultAction: enablePrivateEndpoints ? 'Deny' : 'Allow'
      bypass: 'AzureServices'
    }
  }
}

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storage
  name: 'default'
  properties: {
    deleteRetentionPolicy: {
      enabled: true
      days: 7
    }
  }
}

// Container name matches src/config/search_config.json blob_storage.container_name
resource knowledgeBaseContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: 'ask-hr-knowledge'
  properties: {
    publicAccess: 'None'
  }
}

// ============================================================================
// 9. RBAC Role Assignments (for lab user principal)
// ============================================================================

// Azure AI User — access to AI Foundry project
var azureAIUserRoleId = '53ca6127-db72-4b80-b1b0-d745d6d5456d'
resource aiUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(resourceGroup().id, principalId, azureAIUserRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', azureAIUserRoleId)
    principalId: principalId
    principalType: 'User'
  }
}

// Cognitive Services OpenAI User — invoke OpenAI models
var cognitiveServicesOpenAIUserRoleId = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'
resource openAIUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(resourceGroup().id, principalId, cognitiveServicesOpenAIUserRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAIUserRoleId)
    principalId: principalId
    principalType: 'User'
  }
}

// Search Index Data Contributor — manage search index data
var searchIndexDataContributorRoleId = '8ebe5a00-799e-43f5-93ac-243d3dce84a7'
resource searchDataRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(resourceGroup().id, principalId, searchIndexDataContributorRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataContributorRoleId)
    principalId: principalId
    principalType: 'User'
  }
}

// Search Service Contributor — manage search service
var searchServiceContributorRoleId = '7ca78c08-252a-4471-8644-bb5ff32d4ba0'
resource searchServiceRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(resourceGroup().id, principalId, searchServiceContributorRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchServiceContributorRoleId)
    principalId: principalId
    principalType: 'User'
  }
}

// Storage Blob Data Contributor — read/write blob data
var storageBlobDataContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'
resource storageBlobRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(resourceGroup().id, principalId, storageBlobDataContributorRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
    principalId: principalId
    principalType: 'User'
  }
}

// ============================================================================
// 10. RBAC Role Assignments (for Search Service managed identity)
//     Enables the indexer to pull blobs and call OpenAI embeddings.
// ============================================================================

// Storage Blob Data Reader — search indexer reads blobs via managed identity
var storageBlobDataReaderRoleId = '2a2b9908-6ea1-4ae2-8e65-a410df84e7d1'
resource searchBlobReaderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, search.id, storageBlobDataReaderRoleId)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataReaderRoleId)
    principalId: search.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// Cognitive Services OpenAI User — search skillset calls embedding model via managed identity
resource searchOpenAIUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, search.id, cognitiveServicesOpenAIUserRoleId)
  scope: aiServices
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAIUserRoleId)
    principalId: search.identity.principalId
    principalType: 'ServicePrincipal'
  }
}

// ============================================================================
// 11. Monitoring — Log Analytics + Application Insights
// ============================================================================
resource logAnalytics 'Microsoft.OperationalInsights/workspaces@2023-09-01' = {
  name: '${abbrs.operationalInsightsWorkspaces}${resourceToken}'
  location: location
  properties: {
    sku: { name: 'PerGB2018' }
    retentionInDays: 30
  }
}

resource appInsights 'Microsoft.Insights/components@2020-02-02' = {
  name: '${abbrs.insightsComponents}${resourceToken}'
  location: location
  kind: 'web'
  properties: {
    Application_Type: 'web'
    WorkspaceResourceId: logAnalytics.id
    publicNetworkAccessForIngestion: 'Enabled'
    publicNetworkAccessForQuery: 'Enabled'
  }
}

// ============================================================================
// 12. Key Vault — centralized secrets store
// ============================================================================
resource keyVault 'Microsoft.KeyVault/vaults@2024-04-01-preview' = {
  name: take('${abbrs.keyVaultVaults}${resourceToken}', 24)
  location: location
  properties: {
    tenantId: subscription().tenantId
    sku: { family: 'A', name: 'standard' }
    enableRbacAuthorization: true
    enableSoftDelete: true
    softDeleteRetentionInDays: 7
    enablePurgeProtection: true
    publicNetworkAccess: enablePrivateEndpoints ? 'Disabled' : 'Enabled'
    networkAcls: {
      defaultAction: enablePrivateEndpoints ? 'Deny' : 'Allow'
      bypass: 'AzureServices'
    }
  }
}

// ============================================================================
// 13. Function App — hosts the /api/ask endpoint
// ============================================================================

// User-assigned managed identity for the Function App (used for Azure RBAC)
resource funcIdentity 'Microsoft.ManagedIdentity/userAssignedIdentities@2023-01-31' = {
  name: '${abbrs.managedIdentityUserAssignedIdentities}func-${resourceToken}'
  location: location
}

// Dedicated storage account for Function runtime (separate from data storage)
resource funcStorage 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: take('stf${defaultUniqueSuffix}', 24)
  location: location
  kind: 'StorageV2'
  sku: { name: 'Standard_LRS' }
  properties: {
    allowBlobPublicAccess: false
    minimumTlsVersion: 'TLS1_2'
    supportsHttpsTrafficOnly: true
    allowSharedKeyAccess: true
  }
}

// Linux Consumption (Y1) plan for cost-effective serverless hosting
resource appServicePlan 'Microsoft.Web/serverfarms@2024-04-01' = {
  name: '${abbrs.webServerFarms}${resourceToken}'
  location: location
  kind: 'linux'
  sku: { name: 'Y1', tier: 'Dynamic' }
  properties: {
    reserved: true
  }
}

resource functionApp 'Microsoft.Web/sites@2024-04-01' = {
  name: '${abbrs.webSitesFunctions}${resourceToken}'
  location: location
  kind: 'functionapp,linux'
  identity: {
    type: 'UserAssigned'
    userAssignedIdentities: {
      '${funcIdentity.id}': {}
    }
  }
  properties: {
    serverFarmId: appServicePlan.id
    httpsOnly: true
    virtualNetworkSubnetId: enablePrivateEndpoints ? networking!.outputs.funcSubnetId : null
    siteConfig: {
      linuxFxVersion: 'Python|3.12'
      ftpsState: 'Disabled'
      minTlsVersion: '1.2'
      http20Enabled: true
      cors: {
        // Restrict CORS to Copilot Studio / Teams origins; widen as needed
        allowedOrigins: [
          'https://copilotstudio.microsoft.com'
          'https://make.powerautomate.com'
          'https://teams.microsoft.com'
        ]
      }
      appSettings: [
        { name: 'AzureWebJobsStorage__accountName', value: funcStorage.name }
        { name: 'AzureWebJobsStorage__credential', value: 'managedidentity' }
        { name: 'AzureWebJobsStorage__clientId', value: funcIdentity.properties.clientId }
        { name: 'FUNCTIONS_EXTENSION_VERSION', value: '~4' }
        { name: 'FUNCTIONS_WORKER_RUNTIME', value: 'python' }
        { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: appInsights.properties.ConnectionString }
        { name: 'AZURE_CLIENT_ID', value: funcIdentity.properties.clientId }
        { name: 'AZURE_SEARCH_ENDPOINT', value: 'https://${search.name}.search.windows.net' }
        { name: 'AZURE_OPENAI_ENDPOINT', value: aiServices.properties.endpoint }
        { name: 'AZURE_AI_PROJECT_ENDPOINT', value: '${aiServices.properties.endpoint}/api/projects/${aiProject.name}' }
        { name: 'AZURE_KEY_VAULT_URI', value: keyVault.properties.vaultUri }
        { name: 'AZURE_STORAGE_ACCOUNT_URL', value: 'https://${storage.name}.blob.${environment().suffixes.storage}' }
      ]
    }
  }
}

// ============================================================================
// 14. RBAC for Function App managed identity
// ============================================================================

// Function MI: Storage Blob Data Owner on its runtime storage (for AzureWebJobsStorage MI)
var storageBlobDataOwnerRoleId = 'b7e6dc6d-f1e8-4753-8033-0f276bb0955b'
resource funcStorageOwnerRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(funcStorage.id, funcIdentity.id, storageBlobDataOwnerRoleId)
  scope: funcStorage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataOwnerRoleId)
    principalId: funcIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Function MI: Search Index Data Reader on Search (for query-time access)
var searchIndexDataReaderRoleId = '1407120a-92aa-4202-b7e9-c0e197c71c8f'
resource funcSearchReaderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(search.id, funcIdentity.id, searchIndexDataReaderRoleId)
  scope: search
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', searchIndexDataReaderRoleId)
    principalId: funcIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Function MI: Cognitive Services OpenAI User on AI Services (for embeddings + chat)
resource funcOpenAIUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiServices.id, funcIdentity.id, cognitiveServicesOpenAIUserRoleId)
  scope: aiServices
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAIUserRoleId)
    principalId: funcIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Function MI: Azure AI User on AI Services (for Foundry Project access)
resource funcAIUserRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiServices.id, funcIdentity.id, azureAIUserRoleId)
  scope: aiServices
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', azureAIUserRoleId)
    principalId: funcIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Function MI: Storage Blob Data Reader on knowledge-base storage
resource funcKbBlobReaderRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storage.id, funcIdentity.id, storageBlobDataReaderRoleId)
  scope: storage
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataReaderRoleId)
    principalId: funcIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// Function MI: Key Vault Secrets User
var keyVaultSecretsUserRoleId = '4633458b-17de-408a-b874-0445c86b69e6'
resource funcKvSecretsRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(keyVault.id, funcIdentity.id, keyVaultSecretsUserRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsUserRoleId)
    principalId: funcIdentity.properties.principalId
    principalType: 'ServicePrincipal'
  }
}

// User principal: Key Vault Secrets Officer (manage secrets locally)
var keyVaultSecretsOfficerRoleId = 'b86a8fe4-44ce-4948-aee5-eccb2c155cd7'
resource userKvOfficerRole 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(principalId)) {
  name: guid(keyVault.id, principalId, keyVaultSecretsOfficerRoleId)
  scope: keyVault
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', keyVaultSecretsOfficerRoleId)
    principalId: principalId
    principalType: 'User'
  }
}

// ============================================================================
// 15. Networking (optional) — VNet + Private Endpoints
// ============================================================================
module networking 'modules/networking.bicep' = if (enablePrivateEndpoints) {
  name: 'networking'
  params: {
    location: location
    resourceToken: resourceToken
    searchId: search.id
    storageId: storage.id
    aiServicesId: aiServices.id
    keyVaultId: keyVault.id
  }
}

// ============================================================================
// 16. Function App Entra ID (Easy Auth v2) — optional, for Copilot Studio
// ============================================================================
// When enabled, configures the Function App to validate Entra ID bearer
// tokens. Copilot Studio (or any caller) must obtain an access token for
// `api://<functionAuthClientId>` and present it as `Authorization: Bearer`.
// The Function App's `auth_level=FUNCTION` is still enforced at the route
// level; Easy Auth runs before route execution and rejects invalid tokens.
resource functionAuth 'Microsoft.Web/sites/config@2024-04-01' = if (enableFunctionAuth) {
  parent: functionApp
  name: 'authsettingsV2'
  properties: {
    globalValidation: {
      requireAuthentication: true
      unauthenticatedClientAction: 'Return401'
      redirectToProvider: 'azureactivedirectory'
      excludedPaths: [
        '/api/health'
      ]
    }
    identityProviders: {
      azureActiveDirectory: {
        enabled: true
        registration: {
          openIdIssuer: '${environment().authentication.loginEndpoint}${functionAuthTenantId}/v2.0'
          clientId: functionAuthClientId
        }
        validation: {
          allowedAudiences: [
            'api://${functionAuthClientId}'
            functionAuthClientId
          ]
          defaultAuthorizationPolicy: {
            allowedApplications: union(
              [functionAuthClientId],
              functionAuthAllowedClientIds
            )
          }
        }
      }
    }
    login: {
      tokenStore: {
        enabled: true
      }
    }
    platform: {
      enabled: true
    }
  }
}


// ============================================================================
// Outputs
// ============================================================================
output openAIEndpoint string = aiServices.properties.endpoint
output openAIDeploymentName string = openAIDeploymentName
output gpt5DeploymentName string = gpt5DeploymentName
output embeddingDeploymentName string = embeddingDeploymentName
output aiFoundryResourceName string = aiServices.name
output aiProjectName string = aiProject.name
output projectEndpoint string = '${aiServices.properties.endpoint}/api/projects/${aiProject.name}'
output searchEndpoint string = 'https://${search.name}.search.windows.net'
output searchName string = search.name
output docIntelligenceEndpoint string = docIntelligence.properties.endpoint
output storageAccountName string = storage.name
output functionAppName string = functionApp.name
output functionAppEndpoint string = 'https://${functionApp.properties.defaultHostName}'
output keyVaultName string = keyVault.name
output keyVaultUri string = keyVault.properties.vaultUri
output appInsightsConnectionString string = appInsights.properties.ConnectionString
output logAnalyticsWorkspaceId string = logAnalytics.id
