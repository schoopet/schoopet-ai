# Schoopet Invariants

These are the contracts to review before making architectural changes. Some are enforced by tests or code shape; others are design invariants that should become tests when touched.

## Identity And Scope

1. User-owned data access must be scoped by ADK `tool_context.user_id`.
2. A tool must not accept a user ID supplied by the model as authority for accessing another user's data.
3. Firestore task reads and task result reads must verify `task.user_id == tool_context.user_id`.
4. Session document IDs must be derived from normalized user IDs, with scoped sessions using a deterministic hash of `session_scope`.
5. Channel metadata in ADK session state is routing context, not user instruction text.

## Agent Engine Sessions

1. Gateway-created interactive sessions must include `channel` in Agent Engine session state.
2. Scoped channel sessions must include `session_scope` and any delivery metadata needed by later notifications.
3. Expired sessions must be retired before replacement when a stale Agent Engine session ID exists.
4. Background task sessions must be short-lived and deleted after execution.
5. Memory persistence belongs to the agent callback path; gateway session deletion is cleanup only.

## OAuth And Tokens

1. OAuth state must be short-lived and single-use.
2. OAuth callback handling must not trust a state document after it is expired or marked used.
3. Access tokens live in Firestore; refresh tokens live in Secret Manager.
4. Token refresh must preserve account identity metadata such as email.
5. Rendered OAuth callback HTML must escape interpolated values.
6. Tool-declared scopes and gateway-granted scopes must remain aligned.

Known review items for these invariants are tracked in [oauth-flow-issues.md](/Users/mmontan/schoopet/docs/oauth-flow-issues.md).

## Confirmations And Mutations

1. Calendar creates/updates require confirmation.
2. New Drive/Docs/Sheets creations require confirmation.
3. Writes to existing Sheet, Doc, or Drive folder resources require resource-scoped confirmation unless the resource ID is already approved in session state.
4. The resource confirmation state key prefix is `_resource_confirmed_` and must stay identical between the agent and gateway task executor.
5. Offline background tasks may only bypass confirmation for resource IDs present in `allowed_resource_ids`.
6. `allowed_resource_ids` must be a flat list of concrete resource IDs, not a broad permission class.

## Async Tasks

1. Every async task document ID is the task UUID and must match the stored `task_id`.
2. Valid task statuses are `pending`, `scheduled`, `running`, `completed`, `notified`, `failed`, and `cancelled`.
3. Execution may only claim tasks from `pending` or `scheduled`.
4. Claiming a task must use an optimistic concurrency check so duplicate Cloud Task deliveries cannot execute the same task twice.
5. Cloud Task names should be deterministic for idempotent creation.
6. Scheduled tasks within the Cloud Tasks window must have `cloud_task_name` when queued.
7. Scheduled tasks outside the window may remain without `cloud_task_name` and must be picked up by weekly requeue.
8. Task results should be stored before user notification is attempted.
9. A delivered task should be marked `notified`.
10. Current gateway-owned task execution only supports Discord notifications; non-Discord task delivery must fail clearly until implemented.

## Internal Security

1. Internal endpoints must require `verify_internal_request`.
2. Cloud Tasks calls to `/internal/tasks/execute` must use OIDC with the gateway URL as audience.
3. Cloud Scheduler calls to `/internal/tasks/requeue-scheduled` and email renewal endpoints must use allowed scheduler service accounts.
4. Platform webhooks should validate signatures when secrets/configuration are present and `ENABLE_SIGNATURE_VALIDATION` is true.
5. Secrets must come from Secret Manager or gitignored env files, not checked-in env files.

## Channel Delivery

1. Discord channel tasks must include `discord_channel_id`.
2. Discord async task creation must fail if the session lacks channel context for result delivery.
3. Notification delivery may go through an active agent session, but if the agent requests confirmation or returns empty text, direct delivery must be used where possible.
4. Email-triggered automation should notify only when it took action or needs user input.

## Deployment

1. Terraform creates the Agent Engine shell; `agents/deploy.sh` updates it.
2. Agent deploys must use Python 3.13 to match the configured Agent Engine runtime.
3. Gateway Cloud Run configuration belongs in Terraform, not ad hoc `gcloud run deploy`.
4. Gateway image changes should be deployed by digest to avoid stale `latest` revisions.

## Data And Artifacts

1. Artifact storage requires `ARTIFACT_BUCKET_NAME`; agent artifact service creation should fail clearly if unset.
2. Email attachment artifacts should be stored in GCS, not embedded in Firestore documents.
3. Firestore documents that hold operational state should include timestamps sufficient for debugging lifecycle transitions.
4. Rate limiting must be keyed per normalized user identifier and respect configured exclusions.

## Model And Tooling

1. The deployed root agent is named `personal`.
2. The active root model default is defined in `root_agent.create_agent`.
3. Subagents should be used for search, code execution, structured notes, and deep research rather than adding those concerns directly into gateway code.
4. Gateway code should treat Agent Engine events as structured ADK events, not only concatenated text, because confirmations and tool activity are event-level behavior.
