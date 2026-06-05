// ============================================================================
// Targeted deployment: wire an existing Application Insights resource to an
// existing Foundry project so the portal "Tracing" tab can surface spans.
//
// Scope: resourceGroup (the one that holds the Foundry account).
//
// Deploy with (example):
//   az deployment group create \
//     -g rg-hr-policy-kb-lab-dev \
//     -f infra/bicep/connect-appinsights.bicep \
//     -p foundryAccountName=cog-hr-policy-kb-lab-dev-ovcrqidayerac \
//        foundryProjectName=proj-hr-policy-kb-lab-dev-ovcrqidayerac \
//        appInsightsResourceGroup=rg-hr-policy-kb-lab-dev \
//        appInsightsName=func-hr-policy-kb-lab-dev
// ============================================================================
targetScope = 'resourceGroup'

@description('Name of the existing Microsoft.CognitiveServices/accounts (Foundry / AI Services) resource.')
param foundryAccountName string

@description('Name of the existing Foundry project (child of the account).')
param foundryProjectName string

@description('Resource group containing the existing Application Insights component.')
param appInsightsResourceGroup string = resourceGroup().name

@description('Name of the existing Application Insights component to attach.')
param appInsightsName string

@description('Connection name (logical alias on the project). Keep stable to avoid duplicates.')
param connectionName string = 'appinsights'

// ---------- Existing resources ----------
resource appInsights 'Microsoft.Insights/components@2020-02-02' existing = {
  name: appInsightsName
  scope: resourceGroup(appInsightsResourceGroup)
}

resource foundryAccount 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: foundryAccountName
}

resource foundryProject 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' existing = {
  parent: foundryAccount
  name: foundryProjectName
}

// ---------- Project ↔ App Insights connection (the bit the portal Tracing tab needs) ----------
resource aiProjectAppInsightsConnection 'Microsoft.CognitiveServices/accounts/projects/connections@2025-06-01' = {
  parent: foundryProject
  name: connectionName
  properties: {
    category: 'AppInsights'
    target: appInsights.id
    authType: 'ApiKey'
    isSharedToAll: true
    metadata: {
      ApiType: 'Azure'
      ResourceId: appInsights.id
    }
    credentials: {
      key: appInsights.properties.ConnectionString
    }
  }
}

output connectionId string = aiProjectAppInsightsConnection.id
output appInsightsId string = appInsights.id
