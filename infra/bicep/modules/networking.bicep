// ============================================================================
// Networking module — VNet + Private Endpoints + Private DNS zones
// Enables private connectivity for production deployments.
// ============================================================================

@description('Azure region for all resources')
param location string

@description('Resource token used for naming')
param resourceToken string

@description('Address space for the VNet (CIDR)')
param vnetAddressPrefix string = '10.0.0.0/16'

@description('Subnet prefix for private endpoints')
param peSubnetPrefix string = '10.0.1.0/24'

@description('Subnet prefix for Function App VNet integration')
param funcSubnetPrefix string = '10.0.2.0/24'

@description('Search service resource ID (for PE)')
param searchId string

@description('Storage account resource ID (for PE blob)')
param storageId string

@description('AI Services account resource ID (for PE)')
param aiServicesId string

@description('Key Vault resource ID (for PE)')
param keyVaultId string

// ---------- VNet ----------
resource vnet 'Microsoft.Network/virtualNetworks@2024-07-01' = {
  name: 'vnet-${resourceToken}'
  location: location
  properties: {
    addressSpace: {
      addressPrefixes: [vnetAddressPrefix]
    }
    subnets: [
      {
        name: 'snet-pe'
        properties: {
          addressPrefix: peSubnetPrefix
          privateEndpointNetworkPolicies: 'Disabled'
        }
      }
      {
        name: 'snet-func'
        properties: {
          addressPrefix: funcSubnetPrefix
          delegations: [
            {
              name: 'webapp-delegation'
              properties: {
                serviceName: 'Microsoft.Web/serverFarms'
              }
            }
          ]
        }
      }
    ]
  }
}

// ---------- Private DNS Zones ----------
var dnsZones = [
  'privatelink.search.windows.net'
  'privatelink.blob.${environment().suffixes.storage}'
  'privatelink.cognitiveservices.azure.com'
  'privatelink.openai.azure.com'
  'privatelink.vaultcore.azure.net'
]

resource zones 'Microsoft.Network/privateDnsZones@2020-06-01' = [for zone in dnsZones: {
  name: zone
  location: 'global'
}]

resource zoneLinks 'Microsoft.Network/privateDnsZones/virtualNetworkLinks@2020-06-01' = [for (zone, i) in dnsZones: {
  name: 'link-${uniqueString(vnet.id)}'
  parent: zones[i]
  location: 'global'
  properties: {
    registrationEnabled: false
    virtualNetwork: { id: vnet.id }
  }
}]

// ---------- Private Endpoints ----------
resource peSearch 'Microsoft.Network/privateEndpoints@2024-07-01' = {
  name: 'pe-search-${resourceToken}'
  location: location
  properties: {
    subnet: { id: vnet.properties.subnets[0].id }
    privateLinkServiceConnections: [
      {
        name: 'search'
        properties: {
          privateLinkServiceId: searchId
          groupIds: ['searchService']
        }
      }
    ]
  }
}

resource peSearchDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-07-01' = {
  parent: peSearch
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      { name: 'search', properties: { privateDnsZoneId: zones[0].id } }
    ]
  }
}

resource peStorage 'Microsoft.Network/privateEndpoints@2024-07-01' = {
  name: 'pe-blob-${resourceToken}'
  location: location
  properties: {
    subnet: { id: vnet.properties.subnets[0].id }
    privateLinkServiceConnections: [
      {
        name: 'blob'
        properties: {
          privateLinkServiceId: storageId
          groupIds: ['blob']
        }
      }
    ]
  }
}

resource peStorageDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-07-01' = {
  parent: peStorage
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      { name: 'blob', properties: { privateDnsZoneId: zones[1].id } }
    ]
  }
}

resource peAi 'Microsoft.Network/privateEndpoints@2024-07-01' = {
  name: 'pe-ai-${resourceToken}'
  location: location
  properties: {
    subnet: { id: vnet.properties.subnets[0].id }
    privateLinkServiceConnections: [
      {
        name: 'ai'
        properties: {
          privateLinkServiceId: aiServicesId
          groupIds: ['account']
        }
      }
    ]
  }
}

resource peAiDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-07-01' = {
  parent: peAi
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      { name: 'cog', properties: { privateDnsZoneId: zones[2].id } }
      { name: 'openai', properties: { privateDnsZoneId: zones[3].id } }
    ]
  }
}

resource peKv 'Microsoft.Network/privateEndpoints@2024-07-01' = {
  name: 'pe-kv-${resourceToken}'
  location: location
  properties: {
    subnet: { id: vnet.properties.subnets[0].id }
    privateLinkServiceConnections: [
      {
        name: 'kv'
        properties: {
          privateLinkServiceId: keyVaultId
          groupIds: ['vault']
        }
      }
    ]
  }
}

resource peKvDns 'Microsoft.Network/privateEndpoints/privateDnsZoneGroups@2024-07-01' = {
  parent: peKv
  name: 'default'
  properties: {
    privateDnsZoneConfigs: [
      { name: 'kv', properties: { privateDnsZoneId: zones[4].id } }
    ]
  }
}

output vnetId string = vnet.id
output funcSubnetId string = vnet.properties.subnets[1].id
