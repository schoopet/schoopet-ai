# Schoopet SMS Gateway

A Cloud Run service that bridges multiple messaging channels to the Schoopet agent on Vertex AI Agent Engine.

## Supported Channels

| Channel | Endpoint | Agent |
|---------|----------|-------|
| SMS | `POST /webhook/sms` | Personal |
| WhatsApp | `POST /webhook/sms` | Personal |
| Telegram | `POST /webhook/telegram` | Personal |
| Discord | `POST /webhook/discord` + Gateway WebSocket | Personal |
| Slack | `POST /webhook/slack` | Team |
| Email (Gmail Pub/Sub) | `POST /webhook/email` | Team |

Personal and team agents are separate Vertex AI reasoning engines. The gateway routes each channel to the correct one automatically.

## Adding the Discord Bot to a Server

The bot supports both `/chat` slash commands and @mention / DM conversations.

To add the bot to a Discord server, use this invite link (replace the client ID if the app is re-created):

```
https://discord.com/api/oauth2/authorize?client_id=1495984034268975275&permissions=274877991936&scope=bot+applications.commands
```

> **Note:** The bot must be invited with both `bot` and `applications.commands` scopes. Adding it with only `applications.commands` (the default webhook flow) will make slash commands work but the bot won't appear in the member list and @mentions won't be received.

## Architecture

```
SMS/WhatsApp ──┐
Telegram ──────┤──→ Personal Agent Engine
               │         ↕
               │    Firestore (sessions)
               │         ↕
Slack ─────────┤──→ Team Agent Engine
Email ─────────┘
```

Session state is stored in Firestore (`sms_sessions` collection) with separate session IDs per agent type per user.

## Prerequisites

1. **Google Cloud**
   - Firestore database enabled
   - Secret Manager enabled
   - Two Vertex AI Agent Engines deployed (`PERSONAL_AGENT_ENGINE_ID` and `TEAM_AGENT_ENGINE_ID`)

2. **Twilio** — Account SID, Auth Token, phone number (SMS/WhatsApp)

3. **Telegram** — Bot token from BotFather

4. **Slack** — Bot token and signing secret

## Quick Start

### 1. Configure environment

Copy `environments/wib-boss-finder.env` as a reference for your environment file. Secrets go in `sms-gateway/.env` (gitignored).

Required in `environments/<name>.env`:
```
GOOGLE_CLOUD_PROJECT=...
PERSONAL_AGENT_ENGINE_ID=...   # personal agent (SMS/WhatsApp/Telegram)
TEAM_AGENT_ENGINE_ID=...       # team agent (Slack/Email)
```

### 2. Set up secrets

```bash
./scripts/setup_secrets.sh
```

### 3. Deploy to Cloud Run

```bash
# From repo root:
./sms-gateway/scripts/deploy.sh --env=wib-boss-finder
```

The script loads `environments/<name>.env` then `sms-gateway/.env` (secrets/overrides).

### 4. Configure webhooks

**Twilio** — set SMS webhook to `https://<service-url>/webhook/sms`

**Telegram** — set webhook:
```bash
curl "https://api.telegram.org/bot<TOKEN>/setWebhook?url=https://<service-url>/webhook/telegram"
```

**Slack** — set Request URL in your Slack App to `https://<service-url>/webhook/slack`

**Gmail (Pub/Sub)** — set up watch via:
```
GET /internal/email/setup-watch?topic=projects/<project>/topics/<topic>
```

## Local Development

### Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Set environment variables

```bash
set -a && source ../environments/wib-boss-finder.env && set +a
cp .env.example .env
# Edit .env with your secrets
```

### Run locally

```bash
uvicorn src.main:app --reload --port 8080
```

### Run tests

```bash
pytest tests/ -v
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Service info |
| `/health` | GET | Health check |
| `/webhook/sms` | POST | Twilio SMS/WhatsApp webhook |
| `/webhook/telegram` | POST | Telegram bot webhook |
| `/webhook/slack` | POST | Slack Events API webhook |
| `/webhook/email` | POST | Gmail Pub/Sub push notification |
| `/oauth/google/initiate` | GET | Start Google OAuth flow |
| `/oauth/google/callback` | GET | Google OAuth callback |
| `/internal/task-review` | POST | Async task completion (OIDC-auth) |
| `/internal/user-notify` | POST | Send user notification (OIDC-auth) |
| `/internal/email/setup-watch` | GET | One-time Gmail watch setup |
| `/internal/email/renew-watch` | GET | Renew Gmail watch (Scheduler) |

## Configuration

| Environment Variable | Description | Default |
|---------------------|-------------|---------|
| `GOOGLE_CLOUD_PROJECT` | GCP project ID | Required |
| `GOOGLE_CLOUD_LOCATION` | GCP region | `us-central1` |
| `PERSONAL_AGENT_ENGINE_ID` | Personal agent reasoning engine ID | Required for SMS/Telegram |
| `TEAM_AGENT_ENGINE_ID` | Team agent reasoning engine ID | Required for Slack/Email |
| `SESSION_TIMEOUT_MINUTES` | Session inactivity timeout | `10` |
| `MAX_SMS_SEGMENTS` | Max SMS segments per response | `10` |
| `AGENT_TIMEOUT_SECONDS` | Agent query timeout | `30` |

Secrets (stored in Secret Manager, injected at deploy time):
`TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER`, `TWILIO_WHATSAPP_NUMBER`, `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `TELEGRAM_BOT_TOKEN`, `SLACK_BOT_TOKEN`, `SLACK_SIGNING_SECRET`

## Session Management

- Sessions stored in Firestore (`sms_sessions` collection)
- Each user has separate session IDs for personal and team agents (`personal_agent_session_id`, `team_agent_session_id`)
- Backward compatible: old `agent_session_id` documents are treated as team sessions on first read
- Sessions expire after 10 minutes of inactivity

## Security

- Twilio webhook signatures validated on every request
- Slack requests verified via signing secret
- Internal endpoints (`/internal/*`) require OIDC token or HMAC signature
- Credentials stored in Secret Manager
- Personal and team agents use entirely separate token paths — no cross-contamination possible
- Cloud Run handles HTTPS termination

## Custom Domain

The gateway is accessible at `api.schoopet.com`. To configure a custom domain:

```bash
gcloud beta run domain-mappings create \
  --service schoopet-sms-gateway \
  --domain <your-domain> \
  --region <REGION> \
  --project <PROJECT>
```

Add a CNAME `api → ghs.googlehosted.com.` in your DNS provider. SSL is provisioned automatically.
