# IAM Connectors Adoption Plan

Date: 2026-04-26

## Goal

Adopt Google Cloud Agent Identity auth providers ("IAM connectors") for all end-user OAuth-backed agent functions so that Google-managed credential storage and token retrieval replace the repo's custom OAuth token lifecycle.

This plan covers:

- Interactive end-user auth for personal tools
- ADK `AuthenticatedFunctionTool` / auth-aware MCP usage
- Agent Identity connector provisioning and IAM
- Migration of current Google-backed user tools

This plan does not assume migration of:

- Internal service-to-service auth already handled by Cloud Run / ADC / Agent Identity
- Non-user credentials that do not benefit from 3-legged OAuth
- Background systems that must remain on a dedicated system account until a separate design is approved

## Current State

The repo currently uses a custom Google OAuth stack:

- The SMS gateway initiates OAuth and handles callbacks at `/oauth/google/*`
- Access tokens are stored in Firestore
- Refresh tokens are stored in Secret Manager
- The agent loads and refreshes tokens directly
- Agent tools are mostly plain ADK `FunctionTool`s, not auth-aware tools

Primary code touchpoints:

- `agents/schoopet/oauth_client.py`
- `sms-gateway/src/oauth/manager.py`
- `sms-gateway/src/oauth/handler.py`
- `sms-gateway/src/agent/client.py`
- `agents/schoopet/root_agent.py`
- `agents/schoopet/calendar_tool.py`
- `agents/schoopet/drive_sheets_tool.py`
- `agents/schoopet/email_tool.py`
- `agents/schoopet/deploy.py`

## Target State

The target design is:

- Agent Engine remains deployed with `identity_type=AGENT_IDENTITY`
- Google Cloud IAM connectors hold 3LO provider configuration
- ADK tools request credentials through `GcpAuthProviderScheme`
- The client application handles `adk_request_credential`, browser redirect, consent completion, and conversation resume
- Tool code consumes injected `AuthCredential` values instead of loading refresh tokens from local storage
- The repo's custom token store becomes unnecessary for migrated user functions

## Important Constraint

This is not a storage-only migration.

To adopt IAM connectors, we must change three layers together:

1. Client/gateway auth UX
2. Tool runtime contract
3. Cloud-side connector provisioning and IAM

Without all three, the migration will stall.

## Scope Breakdown

### In scope

- Calendar functions
- Drive functions
- Sheets functions
- Gmail read functions
- Future user-authenticated external functions

### Conditionally in scope

- Email-triggered automation using the user's own Gmail token
- Shared auth helper abstractions in the agent package

### Out of scope for phase 1

- `workspace_system` or other system-account flows that are not clearly modeled as per-user 3LO
- Replacing non-OAuth auth paths
- Removing the current OAuth system before parity is proven

## Adoption Strategy

Use a phased migration with dual-stack operation.

Do not cut over all tools at once. First add connector support and auth-aware client handling, then migrate one low-risk tool family, then expand to the rest, then retire the legacy token stack.

## Workstreams

### 1. Connector provisioning

Deliverables:

- Enable Agent Identity Connector API
- Create one or more 3LO auth providers in each environment
- Register connector callback URLs with the OAuth client
- Grant `roles/iamconnectors.user` to the deployed agent identity
- Document connector names and environment mapping

Decisions to make:

- Single connector for all Google personal scopes vs multiple connectors by capability
- Scope granularity and consent UX
- Connector naming convention per environment

Recommendation:

- Start with one connector for all current personal Google scopes to minimize auth fragmentation
- Revisit scope splitting only after working end-to-end

### 2. Gateway/client auth flow

Deliverables:

- Detect `adk_request_credential` events from Agent Engine responses
- Extract and persist `auth_config`, function call ID, user ID, session ID, and consent nonce
- Present or redirect the user to the returned `auth_uri`
- Add a `continue_uri` endpoint that finalizes consent with `credentials:finalize`
- Resume the conversation by posting the `adk_request_credential` function response back to the agent

Primary files:

- `sms-gateway/src/agent/client.py`
- `sms-gateway/src/main.py`
- New or updated web/gateway handler for `continue_uri`

Notes:

- Current gateway response handling appears text-centric and will need explicit auth-event handling
- This is the critical path for every interactive 3LO tool

### 3. Tool contract migration

Deliverables:

- Replace selected `FunctionTool` wrappers with `AuthenticatedFunctionTool`
- Register `GcpAuthProvider` at agent startup
- Provide `AuthConfig` with `GcpAuthProviderScheme` for each migrated tool
- Refactor tool implementations to consume injected `AuthCredential`
- Remove direct dependency on `OAuthClient` from migrated tools

Primary files:

- `agents/schoopet/root_agent.py`
- `agents/schoopet/calendar_tool.py`
- `agents/schoopet/drive_sheets_tool.py`
- `agents/schoopet/email_tool.py`

Recommended helper additions:

- A shared auth config factory
- A shared token extraction helper from `AuthCredential`
- A migration-safe compatibility layer that can temporarily fall back to legacy auth if the connector flow is unavailable

### 4. Legacy OAuth coexistence

Deliverables:

- Keep current `/oauth/google/*` flow operational during migration
- Introduce feature flags to choose connector auth vs legacy auth per tool or per environment
- Preserve existing Firestore/Secret Manager token logic until all target functions have parity

Recommendation:

- Gate connector-backed tools with explicit environment variables
- Prefer tool-by-tool cutover, not user-by-user hidden behavior

### 5. Testing and rollout

Deliverables:

- Unit tests for auth-event extraction and auth resume behavior
- Tool tests for connector-backed token injection
- End-to-end tests for initial consent, reconnect, expired token refresh, revoked consent, and resumed conversation
- Manual validation in dev and prod-like environments

## Migration Phases

## Phase 0: Foundations

Objective:

Confirm platform prerequisites and define the auth model before code migration.

Tasks:

- Enable required APIs
- Create dev connector(s)
- Record connector resource names
- Confirm deployed agent has Agent Identity and connector IAM access
- Decide whether one connector will cover all current Google personal scopes
- Define feature flags

Exit criteria:

- Connector exists in dev
- Agent principal can use it
- `continue_uri` design is approved

## Phase 1: Client auth plumbing

Objective:

Make the gateway capable of supporting ADK credential requests without changing tool behavior yet.

Tasks:

- Extend event parsing in `sms-gateway/src/agent/client.py`
- Persist auth session state
- Add `continue_uri` endpoint and finalize call
- Add conversation resume path
- Add logging and observability around consent lifecycle

Exit criteria:

- The gateway can detect `adk_request_credential`
- A consent flow can be started and finalized
- A resumed agent request can be sent successfully

## Phase 2: First tool pilot

Objective:

Migrate one small, low-risk function set first.

Recommendation:

- Pilot on Drive file listing or Calendar read-only behavior before Gmail automation

Tasks:

- Register `GcpAuthProvider`
- Add one `AuthenticatedFunctionTool`
- Implement credential injection and bearer-token use
- Verify consent, reuse, refresh, and revoked-access behavior

Exit criteria:

- One production-like tool works end-to-end using connector-managed auth only
- No dependence on Firestore or Secret Manager token retrieval in that tool path

## Phase 3: Google personal tool migration

Objective:

Migrate all user-facing Google functions.

Suggested order:

1. Calendar
2. Drive
3. Sheets
4. Gmail read paths
5. Gmail-triggered automation

Tasks:

- Convert each tool family to `AuthenticatedFunctionTool` or auth-aware MCP equivalent
- Remove `OAuthClient` dependency from migrated paths
- Update prompt/tool descriptions if user auth behavior changes
- Expand tests per tool family

Exit criteria:

- All user-facing Google personal functions use IAM connectors
- Legacy token path is no longer needed for migrated functions

## Phase 4: Legacy stack retirement

Objective:

Remove the old custom token management code after stable operation.

Tasks:

- Stop issuing legacy OAuth links for migrated functions
- Remove Firestore token reads for migrated functions
- Remove refresh token storage for migrated functions
- Delete unused OAuth callback/state logic
- Clean up secrets and Terraform references if fully obsolete

Exit criteria:

- No production traffic depends on legacy personal OAuth storage
- Rollback plan is documented and tested before deletion

## Proposed File-Level Change List

### Agent package

- `agents/schoopet/root_agent.py`
  - Register `GcpAuthProvider`
  - Build shared `AuthConfig`
  - Swap selected `FunctionTool`s to `AuthenticatedFunctionTool`

- `agents/schoopet/calendar_tool.py`
  - Add connector-backed implementation
  - Accept injected `AuthCredential`
  - Remove direct `OAuthClient` dependency after cutover

- `agents/schoopet/drive_sheets_tool.py`
  - Same migration pattern as calendar

- `agents/schoopet/email_tool.py`
  - Use injected auth for Gmail API reads
  - Evaluate special handling for automated email-triggered flows

- `agents/schoopet/oauth_client.py`
  - Keep temporarily for coexistence
  - Remove only after all dependent functions are migrated

### Gateway package

- `sms-gateway/src/agent/client.py`
  - Parse auth request events
  - Support auth resume request content

- `sms-gateway/src/main.py`
  - Wire new auth session components and routes

- `sms-gateway/src/oauth/handler.py`
  - Eventually deprecate for migrated flows

- `sms-gateway/src/oauth/manager.py`
  - Eventually deprecate for migrated flows

### Infra and deployment

- `agents/schoopet/deploy.py`
  - Ensure deployment requirements include Agent Identity auth support if missing

- `terraform/*`
  - Add API enablement and any IAM changes not yet codified
  - Decide whether connector resources are created via Terraform or separately for now

## Risks

### 1. Preview / Pre-GA platform risk

The connector-based 3LO path is still Pre-GA. API shapes, UX, and runtime behavior may change.

Mitigation:

- Keep dual-stack support until stable
- Avoid deleting legacy auth early

### 2. Multi-channel UX mismatch

SMS and chat-first channels do not naturally fit browser redirect flows.

Mitigation:

- Design a clear link-out/link-back flow
- Persist enough state to resume reliably across channels

### 3. Gmail automation edge cases

Interactive user consent is straightforward. Background email-triggered automation is more sensitive because it depends on a usable token outside the immediate interactive moment.

Mitigation:

- Defer Gmail automation cutover until basic interactive tools are proven
- Explicitly validate reconnect and revoked-token behavior

### 4. Scope sprawl

A single broad connector may simplify rollout but increase consent breadth.

Mitigation:

- Start broad for delivery speed
- Revisit splitting after adoption

### 5. Operational blind spots

Without logging around auth events and resume behavior, debugging failures will be difficult.

Mitigation:

- Add structured logs for connector name, session ID, function call ID, consent start, finalize success/failure, and resume outcome

## Feature Flags

Recommended flags:

- `ENABLE_IAM_CONNECTOR_AUTH`
- `IAM_CONNECTOR_GOOGLE_PERSONAL_NAME`
- `IAM_CONNECTOR_CONTINUE_URI`
- `ENABLE_LEGACY_GOOGLE_OAUTH_FALLBACK`
- Optional per-tool overrides if rollout needs finer control

## Rollout Recommendation

Recommended rollout order:

1. Dev environment
2. One pilot tool in dev
3. One pilot tool in prod for internal/test users
4. Calendar and Drive
5. Sheets
6. Gmail read
7. Gmail-triggered automation
8. Legacy auth retirement

## Acceptance Criteria

The migration is complete when:

- All target user-authenticated functions obtain credentials through IAM connectors
- The gateway fully supports auth request, finalize, and resume flow
- No migrated tool depends on Firestore token rows or Secret Manager refresh tokens
- Revoked consent and expired credentials recover correctly
- Legacy custom OAuth code can be removed without user-visible regression

## Open Questions

- Should Google personal scopes live in one connector or multiple connectors?
- Can Gmail-triggered automation safely rely on connector-managed retrieval without additional runtime changes?
- Do we want Terraform to manage connector resources now, or should connector setup remain manual until the API stabilizes?
- Do any current non-browser channels need a specialized auth resume UX?

## Recommended Next Step

Build Phase 1 only:

- add gateway support for `adk_request_credential`
- implement `continue_uri` finalization
- resume the agent conversation

Do not migrate any tool until that plumbing works end-to-end.
