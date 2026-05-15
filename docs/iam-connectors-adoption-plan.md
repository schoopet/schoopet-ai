# IAM Connectors Migration — Completed

Date: 2026-05-13

## Status

**Fully implemented.** All Google Workspace agent tools (Calendar, Drive, Sheets, Docs, Gmail) now use Google Cloud Agent Identity IAM connectors for credential management. The custom OAuth token stack (`oauth_client.py`, Firestore access tokens, Secret Manager refresh tokens) has been removed from the agent tools.

Legacy OAuth (`/oauth/google/*` endpoints, `OAuthManager`) is retained only for the Gmail background push watch setup (`register_gmail_watch`), which runs outside of interactive agent sessions.

## Architecture

### Agent side

- `agents/schoopet/gcp_auth.py` — registers `GcpAuthProvider` at import time (module-level side effect), provides `get_credential_manager()` factory
- `agents/schoopet/root_agent.py` — imports `gcp_auth` to trigger registration before any tool runs
- Tool pattern: each tool class holds a lazy `CredentialManager` instance; `_get_service(tool_context)` calls `cred_mgr.get_auth_credential(tool_context)` → returns service if credential ready, or calls `request_credential(tool_context)` and returns `None`
- Returning `None` causes the tool function to exit early with an empty string; ADK emits `adk_request_credential` to the client
- After user consent, IAM connector stores the token; ADK re-runs the original tool; `GcpAuthProvider._retrieve_credentials()` returns the token from the connector service

### Gateway side

- `sms-gateway/src/agent/client.py` — `AdkCredentialRequest` dataclass, `extract_credential_requests(events)`, `send_credential_response(user_id, session_id, fc_id, auth_config_dict)`
- `sms-gateway/src/session/manager.py` — `pending_credentials` Firestore collection; `set_pending_credential`, `get_pending_credential`, `clear_pending_credential`
- `sms-gateway/src/oauth/handler.py` — `GET /oauth/connector/callback` endpoint; receives nonce from IAM connector redirect, looks up pending credential, calls `send_credential_response`, clears pending
- `sms-gateway/src/discord/handler.py` — `_handle_discord_message` checks `extract_credential_requests` before text extraction; sends auth link if consent needed
- `sms-gateway/src/email/handler.py` — `_route_email_to_agent` checks `extract_credential_requests` in offline mode; stores pending + sends auth link via Discord, does not auto-decline (unlike confirmations)

### Infrastructure

- `terraform/apis.tf` — `iamconnectorcredentials.googleapis.com` enabled
- `terraform/agent_identity.tf` — `google_iam_connector.google_personal` resource; `roles/iamconnectors.user` granted to agent principal
- `terraform/variables.tf` — `google_oauth_client_id`, `google_oauth_client_secret`
- `terraform/sms_gateway.tf` — `IAM_CONNECTOR_GOOGLE_PERSONAL_NAME`, `IAM_CONNECTOR_CONTINUE_URI` env vars
- `environments/prod.env`, `environments/dev.env` — connector name and continue URI set

## Flow

### First use (consent required)

1. User sends a message that triggers a Google Workspace tool
2. Agent tool calls `_get_service(tool_context)` → `CredentialManager.get_auth_credential(tool_context)`
3. `GcpAuthProvider._retrieve_credentials(user_id, auth_scheme)` → IAM connector returns `metadata.uri_consent_required` with `authorization_uri` and `consent_nonce`
4. `CredentialManager` stores credential with `auth_uri` in `auth_config.exchanged_auth_credential`, returns `None`
5. Tool calls `request_credential(tool_context)` → ADK emits `adk_request_credential` event
6. Gateway's `extract_credential_requests(events)` finds the event, extracts `auth_uri` and `nonce`
7. Gateway stores `pending_credentials/{nonce}` in Firestore
8. Gateway sends user: "To use this feature I need access to your Google account. Click here: {auth_uri}"
9. User clicks link → Google consent → IAM connector callback → IAM connector stores token → redirect to `{continue_uri}?state={nonce}`
10. `GET /oauth/connector/callback?state={nonce}` → look up pending credential → call `send_credential_response` → clear pending
11. `send_credential_response` posts `adk_request_credential` function response → ADK re-runs original tool
12. `GcpAuthProvider._retrieve_credentials` → IAM connector now has token → returns `AuthCredential(http=...)`
13. Tool gets service, executes, returns result

### Subsequent use (token cached by IAM connector)

Steps 1–2: same as above  
Step 3: IAM connector returns token immediately (`operation.done = True`)  
Steps 4–13: skipped, tool executes directly

## Key decisions

- `AuthenticatedFunctionTool` was NOT used — it lacks `require_confirmation` support. `CredentialManager` is called directly inside `FunctionTool` async functions, which preserves all confirmation behavior.
- `CredentialManager` is lazily initialized per tool class (not in `__init__`) to avoid Agent Engine pickling issues. Each tool call creates or reuses the instance.
- `GcpAuthProvider` is registered once at module import via `gcp_auth.py`; it's a class-level registry so it persists across `CredentialManager` instances.
- The `nonce` field from `GcpAuthProvider`'s `OAuth2Auth` response is used as the Firestore document ID in `pending_credentials` for the connector callback correlation.
- Background email processing (offline mode) does NOT auto-decline credential requests — instead it stores the pending credential and sends the auth link via Discord, so the user can authorize and the pending action resumes automatically.
- Legacy `/oauth/google/*` routes and `OAuthManager` are kept for Gmail push watch setup only.

## Files changed

### Agent package
- `agents/schoopet/gcp_auth.py` — NEW
- `agents/schoopet/oauth_client.py` — DELETED
- `agents/schoopet/root_agent.py` — added `gcp_auth` import
- `agents/schoopet/calendar_tool.py` — migrated to `CredentialManager`
- `agents/schoopet/drive_sheets_tool.py` — migrated to `CredentialManager`
- `agents/schoopet/email_tool.py` — migrated to `CredentialManager`
- `agents/schoopet/requirements.txt` — `google-adk[agent-identity]`

### Gateway package
- `sms-gateway/src/config.py` — `IAM_CONNECTOR_GOOGLE_PERSONAL_NAME`, `IAM_CONNECTOR_CONTINUE_URI`
- `sms-gateway/src/agent/client.py` — `AdkCredentialRequest`, `extract_credential_requests`, `send_credential_response`
- `sms-gateway/src/session/manager.py` — `PENDING_CREDENTIALS_COLLECTION`, 3 pending credential methods
- `sms-gateway/src/oauth/handler.py` — `GET /oauth/connector/callback`, updated `init_oauth_services`
- `sms-gateway/src/discord/handler.py` — credential request handling in `_handle_discord_message`
- `sms-gateway/src/email/handler.py` — credential request handling in `_route_email_to_agent`
- `sms-gateway/src/main.py` — passes `agent_client` to `init_oauth_services`

### Infrastructure
- `terraform/apis.tf` — `iamconnectorcredentials.googleapis.com`
- `terraform/agent_identity.tf` — `google_iam_connector`, `google_iam_connector_iam_member`
- `terraform/variables.tf` — `google_oauth_client_id`, `google_oauth_client_secret`
- `terraform/sms_gateway.tf` — IAM connector env vars
- `environments/prod.env`, `environments/dev.env` — connector name and continue URI

## Deployment checklist

Before deploying to an environment:

1. Provision the IAM connector via Terraform (or manually if Pre-GA Terraform support is unavailable):
   - Enable `iamconnectorcredentials.googleapis.com`
   - Create connector with OAuth client ID/secret and scopes
   - Grant `roles/iamconnectors.user` to agent principal
   - Add `{gateway_url}/oauth/connector/callback` as authorized redirect URI in Google Cloud Console

2. Set `google_oauth_client_id` and `google_oauth_client_secret` in Terraform `.tfvars` or secrets

3. Deploy agent: `./agents/deploy.sh --env=prod` (picks up `[agent-identity]` extra and `gcp_auth.py`)

4. Deploy gateway: `./sms-gateway/scripts/deploy.sh --env=prod` (picks up new env vars and `/oauth/connector/callback` endpoint)

5. Smoke test: trigger a Calendar or Drive tool call in Discord; verify consent link is sent; click link; verify tool executes on follow-up message

## Open items

- Terraform resource type `google_iam_connector` is for the Pre-GA API — confirm exact resource type name when API becomes GA or if manual provisioning is needed initially
- Gmail push watch (`register_gmail_watch`) still uses legacy OAuth; once IAM connector path is proven, it can be migrated too
- Background email offline mode sends auth link via Discord; a future improvement could track the nonce to auto-resume the email processing after consent
