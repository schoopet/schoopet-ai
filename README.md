# schoopet-ai

A multi-component AI assistant system built on Google Vertex AI Agent Engine.

## Components

| Component | Path | Description |
|-----------|------|-------------|
| Agent | `agents/schoopet/` | Google ADK agent — handles all channels via personal OAuth tokens |
| SMS Gateway | `sms-gateway/` | FastAPI Cloud Run service routing all channels to the agent |
| Task Worker | `task-worker/` | Async task executor for long-running agent operations |
| Website | `website/` | Static Vite landing page |

All channels (SMS, WhatsApp, Telegram, Discord, Slack, Email) route to the single personal agent using `PERSONAL_AGENT_ENGINE_ID`. The agent is deployed as a Vertex AI reasoning engine and managed via Terraform.

## Environments

Project settings live in `environments/` (checked in, no secrets):

| File | GCP Project | Use |
|------|-------------|-----|
| `environments/dev.env` | `schoopet-dev` | Development |
| `environments/prod.env` | `schoopet-prod` | Production |

Secrets go in `environments/<name>.secrets.env` (gitignored). The deploy scripts load both files automatically.

## Deploying

```bash
# Agent (updates existing engine — engine must be created via Terraform first)
./agents/deploy.sh --env=prod

# SMS Gateway
./sms-gateway/scripts/deploy.sh --env=prod

# Task Worker
./task-worker/deploy.sh --env=prod
```

## First-time setup for a new environment

### 1. Create the env file

Create `environments/<name>.env` with at minimum `GOOGLE_CLOUD_PROJECT` set. Use `environments/prod.env` as a reference.

### 2. Create infrastructure via Terraform

```bash
cd terraform
terraform init -backend-config="bucket=<state-bucket>" -backend-config="prefix=<env>"
terraform apply -var-file=environments/<name>.tfvars
```

This creates the Agent Engine (reasoning engine), Cloud Run services, Cloud Tasks queue, Pub/Sub topics, and all required IAM bindings. After applying, copy the printed `personal_agent_resource_name` output into `environments/<name>.env` as `PERSONAL_AGENT_ENGINE_ID`.

### 3. Deploy the agent

```bash
./agents/deploy.sh --env=<name>
```

### 4. Domain and networking setup

The SMS Gateway needs a public HTTPS URL for OAuth callbacks and messaging webhooks. There are two options:

**Option A — use the Cloud Run default URL (recommended for dev)**

Skip custom DNS entirely. After deploying the SMS Gateway, set `OAUTH_BASE_URL` in `environments/<name>.env` to the printed Cloud Run URL (`https://shoopet-sms-gateway-xxx-uc.a.run.app`). The URL changes only on a full service replacement, not on normal redeployments.

**Option B — custom subdomain (recommended for prod)**

Add a DNS record pointing the subdomain to Cloud Run:

```
CNAME  api   ghs.googlehosted.com.
```

Then create a domain mapping in the GCP project:

```bash
gcloud beta run domain-mappings create \
  --service shoopet-sms-gateway \
  --domain api.schoopet.com \
  --region us-central1 \
  --project <GOOGLE_CLOUD_PROJECT>
```

Note: GCP requires domain ownership verification **per project**, even if the same Google account owns all projects. Verify via [Search Console](https://search.google.com/search-console).

### 5. Register the OAuth redirect URI

`GOOGLE_OAUTH_REDIRECT_URI` is automatically set to `${OAUTH_BASE_URL}/oauth/google/callback` by the deploy script. This exact URI must be registered in the Google Cloud Console OAuth 2.0 client before users can authorize.

Go to **APIs & Services → Credentials → OAuth 2.0 Client** and add:
```
https://<your-gateway-url>/oauth/google/callback
```

### 6. Store secrets in Secret Manager

The SMS Gateway reads these secrets at runtime:

```bash
./sms-gateway/scripts/setup_secrets.sh
```

Required secrets: `twilio-account-sid`, `twilio-auth-token`, `twilio-phone-number`, `twilio-whatsapp-number`, `google-oauth-client-id`, `google-oauth-client-secret`, `telegram-bot-token`, `slack-bot-token`, `slack-signing-secret`.

### 7. Deploy SMS Gateway and Task Worker

```bash
./sms-gateway/scripts/deploy.sh --env=<name>
./task-worker/deploy.sh --env=<name>
```

Update `environments/<name>.env` with the Task Worker URL printed after deploy.

### 8. Configure messaging webhooks

Each environment needs its webhooks pointed at its own gateway URL. These are platform-side settings — no code change required:

| Integration | Where to configure | Setting |
|---|---|---|
| Twilio SMS | Twilio Console → Phone Number → Messaging webhook | `https://<gateway-url>/webhook/sms` |
| Telegram | Call `setWebhook` via Bot API | `https://<gateway-url>/webhook/telegram` |
| Slack | Slack App settings → Event Subscriptions | `https://<gateway-url>/webhook/slack` |
| Discord | Discord Developer Portal → General Information → Interactions Endpoint URL | `https://<gateway-url>/webhook/discord` |

**Adding the Discord bot to a server**

Use this invite link to add the bot to a Discord server:

```
https://discord.com/oauth2/authorize?client_id=1495984034268975275&scope=bot+applications.commands&permissions=2048
```

Once added, users can talk to the agent by DMing the bot directly, @mentioning it in any channel, or using the `/chat` slash command.

> **Note:** Enable **Message Content Intent** in the Discord Developer Portal under Bot → Privileged Gateway Intents, otherwise the bot cannot read message text.
