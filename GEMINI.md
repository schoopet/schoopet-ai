# Schoopet Project Context

Schoopet is a personal workflow assistant built on Google ADK, Vertex AI Agent Engine, and a FastAPI Cloud Run gateway.

## Active Components

### Agent: `agents/schoopet/`

- ADK `LlmAgent` named `personal`.
- Deployed as a Vertex AI Agent Engine app via `AdkApp`.
- Default model is `gemini-3-flash-preview` through `GlobalGemini`.
- Uses Vertex AI Memory Bank and explicit memory tools.
- Uses Google Calendar, Drive, Docs, Sheets, and Gmail through the repo's custom OAuth token stack.
- Has subagents for search, code execution, structured notes, and deep research.
- Creates background work through `AsyncTaskTool` and Cloud Tasks.

### Gateway: `sms-gateway/`

- FastAPI Cloud Run service.
- Handles Discord, Telegram, Slack, Gmail Pub/Sub, OAuth, and internal task execution endpoints.
- Owns session storage in Firestore.
- Owns OAuth initiation and callbacks.
- Handles ADK confirmations.
- Executes background tasks at `/internal/tasks/execute`.

### Web: `web/`

- Static Vite site.
- Deployed through Firebase Hosting with `deploy_web.sh`.

### Infrastructure: `terraform/`

- Creates Agent Engine shell, Cloud Run gateway, Cloud Tasks, Cloud Scheduler, Pub/Sub, Artifact Registry, Secret Manager secrets, GCS artifacts, Firestore config, service accounts, and IAM.
- Agent deploy updates the existing reasoning engine after Terraform creates it.

## Request Flow

```text
Channel webhook -> sms-gateway -> Agent Engine session -> ADK agent/tools
Cloud Task ------> sms-gateway -> temporary Agent Engine session -> task result
```

Firestore stores sessions, OAuth state/token metadata, pending approvals, and async task documents. Secret Manager stores refresh tokens and channel/API secrets. GCS stores artifacts.

## Common Commands

Run tests:

```bash
make test
```

Run the agent locally:

```bash
cd agents
set -a && source ../environments/dev.env && set +a
python -m schoopet.main
```

Deploy the agent:

```bash
./agents/deploy.sh --env=prod
```

Deploy the gateway:

```bash
./sms-gateway/scripts/deploy.sh --env=prod
```

Run the web app:

```bash
cd web
npm install
npm run dev
```

## Review Docs

- Architecture: [docs/architecture.md](/Users/mmontan/schoopet/docs/architecture.md)
- Invariants: [docs/invariants.md](/Users/mmontan/schoopet/docs/invariants.md)
