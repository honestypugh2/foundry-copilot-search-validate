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
    publicNetworkAccess: 'Enabled'
    allowProjectManagement: true
    disableLocalAuth: false
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
    semanticSearch: 'free'
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
  properties: {
    customSubDomainName: '${abbrs.cognitiveServicesFormRecognizer}${resourceToken}'
    publicNetworkAccess: 'Enabled'
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

// Container name matches src_lab/config/search_config.json blob_storage.container_name
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
