# Schoopet

Schoopet is a personal workflow assistant built around a single Google Vertex AI Agent Engine deployment. Users interact with the agent through Discord, with Gmail-triggered automation feeding notifications back to Discord. The agent can use memory, Google Calendar, Drive, Docs, Sheets, Gmail, search, code execution, and background tasks.

## Components

| Component | Path | Runtime | Purpose |
|---|---|---|---|
| Agent | `agents/schoopet/` | Vertex AI Agent Engine | ADK agent, tools, subagents, memory, deployment code |
| Gateway | `sms-gateway/` | Cloud Run / FastAPI | Webhooks, OAuth, sessions, confirmations, async task execution |
| Web | `web/` | Firebase Hosting / Vite | Static website, signup, privacy, terms |
| Infrastructure | `terraform/` | Terraform | Agent Engine shell, Cloud Run, Cloud Tasks, Pub/Sub, IAM, secrets, storage |

The active production path is:

```text
User channel -> sms-gateway -> Vertex AI Agent Engine -> tools/subagents
                                 ^
Cloud Tasks -> sms-gateway -------|
```

There is one active agent engine per environment: `PERSONAL_AGENT_ENGINE_ID`.

## Architecture

See [docs/architecture.md](/Users/mmontan/schoopet/docs/architecture.md) for the current architecture description and request flows.

Key points:

- The gateway owns active channel ingress and keeps channel/session metadata in Firestore.
- The agent owns reasoning, tool selection, memory, and Google Workspace actions.
- Google user OAuth is still custom: access tokens in Firestore, refresh tokens in Secret Manager.
- Background tasks are stored in Firestore, scheduled with Cloud Tasks, and executed by the gateway at `/internal/tasks/execute`.
- Long-delay scheduled work beyond the Cloud Tasks 30-day window is requeued weekly by Cloud Scheduler.

## Invariants

See [docs/invariants.md](/Users/mmontan/schoopet/docs/invariants.md) for the review checklist of contracts that should stay true as the system changes.

The highest-risk invariants are:

- User-scoped data access must always be keyed by ADK `tool_context.user_id`.
- OAuth state must be single-use and short-lived.
- Background task execution must be claimed atomically before work starts.
- Offline Drive/Docs/Sheets writes may only target resource IDs explicitly approved at scheduling time.
- Cloud Tasks and scheduler calls must reach internal endpoints with valid OIDC/HMAC auth.

## Environments

Non-secret environment files live in `environments/`:

| File | Project | Use |
|---|---|---|
| `environments/dev.env` | `schoopet-dev` | Development |
| `environments/prod.env` | `schoopet-prod` | Production |

Optional `environments/<name>.secrets.env` files are gitignored and loaded after the checked-in env file by deploy scripts.

Important variables:

| Variable | Used by | Meaning |
|---|---|---|
| `GOOGLE_CLOUD_PROJECT` | all services | GCP project |
| `GOOGLE_CLOUD_LOCATION` | all services | GCP region, usually `us-central1` |
| `PERSONAL_AGENT_ENGINE_ID` | agent/gateway | Vertex AI reasoning engine ID or resource suffix |
| `ARTIFACT_BUCKET_NAME` | agent/gateway | GCS bucket for ADK artifacts and email attachments |
| `OAUTH_BASE_URL` | gateway/deploy | Public base URL used for OAuth callbacks |
| `SMS_GATEWAY_URL` | agent/tasks | Cloud Run gateway URL used by Cloud Tasks |
| `SMS_GATEWAY_SA` | agent/tasks | Service account used for OIDC task calls |
| `EMAIL_PUBSUB_TOPIC` | gateway | Gmail Pub/Sub topic |

## Development

Install and run gateway tests:

```bash
make test
```

Run only gateway tests:

```bash
make test-sms-gateway
```

Agent development uses the `agents/` directory as the package parent:

```bash
cd agents
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r schoopet/requirements.txt

set -a && source ../environments/dev.env && set +a
python -m schoopet.main
```

Always run agent modules with `python -m schoopet.<module>` from `agents/` so relative imports resolve correctly.

Run the web app locally:

```bash
cd web
npm install
npm run dev
```

## Deployment

Terraform creates the durable cloud resources, including the Agent Engine shell. The agent deploy updates that existing engine.

First-time infrastructure:

```bash
cd terraform
terraform init -backend-config="bucket=<state-bucket>" -backend-config="prefix=<env>"
terraform apply -var-file=environments/<env>.tfvars
```

Agent deploy:

```bash
./agents/deploy.sh --env=prod
```

Requirements:

- `agents/.venv` must use Python 3.13.
- `PERSONAL_AGENT_ENGINE_ID` must be set or discoverable from Terraform output.

Gateway deploy:

```bash
./sms-gateway/scripts/deploy.sh --env=prod
```

The gateway deploy builds the container with Cloud Build, resolves the image digest, and applies Terraform with `sms_gateway_image`.

Web deploy:

```bash
./deploy_web.sh
```

## Channel Webhooks

Configure external integrations to call the gateway:

| Integration | Endpoint |
|---|---|
| Discord interactions | `POST https://<gateway>/webhook/discord` |
| Gmail Pub/Sub push | `POST https://<gateway>/webhook/email` |

Discord DM and mention handling also uses the gateway's Discord Gateway WebSocket when Discord secrets are configured.

## Historical Notes

Existing design notes remain in `docs/`:

- [OAuth flow issues](/Users/mmontan/schoopet/docs/oauth-flow-issues.md)
- [IAM connectors adoption plan](/Users/mmontan/schoopet/docs/iam-connectors-adoption-plan.md)
