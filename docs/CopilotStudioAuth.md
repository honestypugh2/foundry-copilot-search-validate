# Copilot Studio → Function App (Entra ID AAD Authentication)

Production setup so Copilot Studio calls the Function App with a bearer token
instead of a static function key. The Function App validates the token at the
platform layer (Easy Auth v2) before any code runs.

## 1 — Register the Function App in Entra ID

```bash
APP_NAME="hr-policy-func-api"
APP_ID=$(az ad app create --display-name "$APP_NAME" --query appId -o tsv)
SP_ID=$(az ad sp create --id "$APP_ID" --query id -o tsv)

# Expose an API scope so Copilot Studio can request a token for it
az ad app update --id "$APP_ID" --identifier-uris "api://$APP_ID"
az ad app update --id "$APP_ID" --set api='{
  "oauth2PermissionScopes":[{
    "id":"'$(uuidgen)'",
    "adminConsentDescription":"Call the HR Policy Function API",
    "adminConsentDisplayName":"Call HR Policy API",
    "isEnabled":true,
    "type":"User",
    "value":"access_as_user"
  }]
}'

echo "Function App client ID: $APP_ID"
```

## 2 — Deploy infra with Easy Auth enabled

Set the azd env vars before `azd up` / `azd provision`:

```bash
azd env set ENABLE_FUNCTION_AUTH true
azd env set AZURE_FUNCTION_AUTH_CLIENT_ID "$APP_ID"
azd up
```

Easy Auth will now reject any request to `/api/ask` without a valid bearer
token for audience `api://<APP_ID>`. The `/api/health` route is excluded.

## 3 — Register Copilot Studio as an allowed caller

Copilot Studio's first-party app ID is **`38e2b35e-2ae8-48c9-9c8a-cb0a1ba27cdc`**
(Power Virtual Agents). Add it to `functionAuthAllowedClientIds` in
`infra/main.parameters.json`:

```json
"functionAuthAllowedClientIds": {
  "value": ["38e2b35e-2ae8-48c9-9c8a-cb0a1ba27cdc"]
}
```

Re-run `azd provision` to apply.

> If your tenant uses a custom Copilot Studio connector app registration,
> use that app's client ID instead.

## 4 — Wire the OpenAPI connector in Copilot Studio

1. Open your copilot → **Tools** → **+ Add a tool** → **New tool** → **Custom connector**.
2. Choose **Import from OpenAPI file** and upload [copilot/openapi.yaml](../copilot/openapi.yaml).
3. On the **Security** tab, select **OAuth 2.0** → **Azure Active Directory**:
   - **Client ID**: `<APP_ID>` from step 1
   - **Client secret**: a secret you create under the app's **Certificates & secrets** blade
   - **Login URL**: `https://login.microsoftonline.com`
   - **Tenant ID**: your tenant GUID
   - **Resource URL** / **Scope**: `api://<APP_ID>/.default`
4. Save, then **Test** the connector — Copilot Studio will trigger an admin
   consent flow on first use.

## 5 — Verify

```bash
# Get a token as your user (interactive)
TOKEN=$(az account get-access-token --resource "api://$APP_ID" --query accessToken -o tsv)

# Call the Function
curl -X POST "https://<func-name>.azurewebsites.net/api/ask" \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query":"What is the PTO policy?"}'
```

A 401 means Easy Auth rejected the token (wrong audience, expired, or app not
in `allowedApplications`). A 200 with JSON means auth is wired correctly.

## Rollback

Set `enableFunctionAuth=false` and re-provision. The Function App falls back to
function-key auth (the `auth_level=FUNCTION` on the route).
