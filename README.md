# schoopet-ai

A multi-component AI assistant system built on Google Vertex AI Agent Engine.

## Components

| Component | Path | Description |
|-----------|------|-------------|
| Python Agent | `agents/schoopet/` | Google ADK multi-agent with memory, calendar, smart home, Drive/Sheets |
| SMS Gateway | `sms-gateway/` | FastAPI Cloud Run service bridging Twilio/Slack/Telegram to the agent |
| Task Worker | `task-worker/` | Async task executor for long-running agent operations |
| Website | `web/` | Static Vite landing page |

## Environments

Project settings live in `environments/` (checked in, no secrets):

| File | GCP Project | Use |
|------|-------------|-----|
| `environments/dev.env` | `schoopet-dev` | Development |
| `environments/prod.env` | `schoopet-prod` | Production |
| `environments/wib-boss-finder.env` | `mmontan-ml` | Current live environment |

Secrets and local overrides go in component-level `.env` files (gitignored):
- `agents/schoopet/.env`
- `sms-gateway/.env`

## Deploying

```bash
# Agent (defaults to --env=dev)
./agents/deploy.sh --env=wib-boss-finder

# SMS Gateway
./sms-gateway/scripts/deploy.sh --env=wib-boss-finder

# Task Worker
./task-worker/deploy.sh --env=wib-boss-finder
```

Each script loads `environments/<name>.env` first, then the component `.env` for secrets.
Pass `--new` to `agents/deploy.sh` to create a fresh Agent Engine instead of updating.

## First-time setup for a new environment

### 1. Create the env file

Create `environments/<name>.env` with at minimum `GOOGLE_CLOUD_PROJECT` set. Use `environments/wib-boss-finder.env` as a reference.

### 2. Deploy the agent

```bash
./agents/deploy.sh --env=<name> --new
```

Copy the printed `GOOGLE_CLOUD_AGENT_ENGINE_ID` into `environments/<name>.env`.

### 3. Domain and networking setup

The SMS Gateway needs a public HTTPS URL for OAuth callbacks and messaging webhooks. There are two options:

**Option A — use the Cloud Run default URL (recommended for dev)**

Skip custom DNS entirely. After deploying the SMS Gateway, set `OAUTH_BASE_URL` in `environments/<name>.env` to the printed Cloud Run URL (`https://shoopet-sms-gateway-xxx-uc.a.run.app`). The URL changes only on a full service replacement, not on normal redeployments.

**Option B — custom subdomain (recommended for prod)**

Add a DNS record pointing the subdomain to Cloud Run:

```
CNAME  dev   ghs.googlehosted.com.
```

Then create a domain mapping in the new GCP project:

```bash
gcloud beta run domain-mappings create \
  --service shoopet-sms-gateway \
  --domain dev.schoopet.com \
  --region us-central1 \
  --project <GOOGLE_CLOUD_PROJECT>
```

Note: GCP requires domain ownership verification **per project**, even if the same Google account owns all projects. Verify via [Search Console](https://search.google.com/search-console) (an HTML file or meta tag on the domain). The same Google account can verify the same domain in multiple projects.

### 4. Register the OAuth redirect URI

`GOOGLE_OAUTH_REDIRECT_URI` is automatically set to `${OAUTH_BASE_URL}/oauth/google/callback` by the deploy script. This exact URI must be registered in the Google Cloud Console OAuth 2.0 client before users can authorize.

Go to **APIs & Services → Credentials → OAuth 2.0 Client** and add:
```
https://<your-gateway-url>/oauth/google/callback
```

**Two approaches for the OAuth client itself:**

- **Shared client** (simpler): Add the new redirect URI to the existing client in `mmontan-ml`. Copy the same `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` values into the new project's Secret Manager under the secret names `google-oauth-client-id` and `google-oauth-client-secret`.
- **Per-project client** (cleaner isolation): Create a new OAuth client in the new GCP project. Store its credentials in that project's Secret Manager under the same secret names. The agent's `oauth_client.py` picks them up automatically via the Secret Manager fallback.

### 5. Store secrets in the new project's Secret Manager

The SMS Gateway reads these secrets at runtime:

```bash
# Run setup_secrets.sh or create them manually:
./sms-gateway/scripts/setup_secrets.sh
```

Required secrets: `twilio-account-sid`, `twilio-auth-token`, `twilio-phone-number`, `twilio-whatsapp-number`, `google-oauth-client-id`, `google-oauth-client-secret`, `telegram-bot-token`, `slack-bot-token`, `slack-signing-secret`.

### 6. Deploy SMS Gateway and Task Worker

```bash
./sms-gateway/scripts/deploy.sh --env=<name>
./task-worker/deploy.sh --env=<name>
```

Update `environments/<name>.env` with the Task Worker URL printed after deploy.

### 7. Configure messaging webhooks

Each environment needs its webhooks pointed at its own gateway URL. These are platform-side settings — there is no code change:

| Integration | Where to configure | Setting |
|---|---|---|
| Twilio SMS | Twilio Console → Phone Number → Messaging webhook | `https://<gateway-url>/webhook/sms` |
| Telegram | Call `setWebhook` via Bot API | `https://<gateway-url>/webhook/telegram` |
| Slack | Slack App settings → Event Subscriptions | `https://<gateway-url>/webhook/slack` |

For `dev`, the pragmatic approach is to use a separate Twilio test number and separate Telegram/Slack apps. Sharing a single number/bot across environments is not recommended as webhooks can only point to one URL at a time.
