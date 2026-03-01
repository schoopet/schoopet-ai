# Schoopet SMS Gateway

A Cloud Run service that integrates Twilio SMS with the Schoopet agent deployed on Vertex AI Agent Engine.

## Features

- Receives SMS via Twilio webhooks
- Routes messages to Schoopet agent on Vertex AI
- Maintains conversation sessions with 10-minute timeout
- Splits long responses into multiple SMS messages
- Validates Twilio webhook signatures for security

## Architecture

```
User SMS → Twilio → Cloud Run (this service) → Agent Engine
                         ↓
                    Firestore (session state)
                         ↓
            Agent Engine → Cloud Run → Twilio → User SMS
```

## Prerequisites

1. **Google Cloud Setup**
   - Project with Vertex AI Agent Engine deployed
   - Firestore database enabled
   - Secret Manager enabled

2. **Twilio Account**
   - Account SID and Auth Token
   - Phone number for sending/receiving SMS

## Quick Start

### 1. Configure environment

Copy or create `environments/<name>.env` at the repo root (see `environments/wib-boss-finder.env` as reference).
Secrets go in `sms-gateway/.env` (gitignored).

### 2. Set Up Secrets

```bash
./scripts/setup_secrets.sh
```

This creates secrets in Secret Manager for Twilio credentials.

### 3. Set Up Firestore

```bash
cd scripts
python setup_firestore.py
```

### 4. Deploy to Cloud Run

```bash
# From repo root:
./sms-gateway/scripts/deploy.sh --env=wib-boss-finder   # or --env=dev, --env=prod
```

The script loads `environments/<name>.env` (project settings) then `sms-gateway/.env` (secrets).

### 5. Configure Twilio

1. Go to [Twilio Console](https://console.twilio.com)
2. Navigate to your phone number settings
3. Set the webhook URL: `https://<your-service-url>/webhook/sms`
4. Set HTTP method to `POST`

## Local Development

### Install Dependencies

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Set Environment Variables

Project-level settings come from `environments/<name>.env` at the repo root.
Create a `sms-gateway/.env` for local secrets (see `.env.example`):

```bash
cp .env.example .env
# Edit .env with your secret values (Twilio, OAuth client, Slack, etc.)
```

Then source the environment before running locally:

```bash
set -a && source ../environments/wib-boss-finder.env && set +a
```

### Run Locally

```bash
uvicorn src.main:app --reload --port 8080
```

### Run Tests

```bash
pytest tests/ -v
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Service info |
| `/health` | GET | Health check |
| `/webhook/sms` | POST | Twilio webhook endpoint |

## Configuration

| Environment Variable | Description | Default |
|---------------------|-------------|---------|
| `GOOGLE_CLOUD_PROJECT` | GCP project ID | Required |
| `GOOGLE_CLOUD_LOCATION` | GCP region | `us-central1` |
| `AGENT_ENGINE_ID` | Agent Engine resource ID | Required |
| `TWILIO_ACCOUNT_SID` | Twilio Account SID | Required |
| `TWILIO_AUTH_TOKEN` | Twilio Auth Token | Required |
| `TWILIO_PHONE_NUMBER` | Twilio phone number | Required |
| `SESSION_TIMEOUT_MINUTES` | Session inactivity timeout | `10` |
| `MAX_SMS_SEGMENTS` | Max SMS per response | `10` |
| `AGENT_TIMEOUT_SECONDS` | Agent query timeout | `30` |

## Session Management

- Sessions are stored in Firestore (`sms_sessions` collection)
- Each phone number gets a unique session
- Sessions expire after 10 minutes of inactivity
- New messages after timeout create a new session

## Message Handling

- Long responses are split into 160-character SMS segments
- Multi-part messages get `(1/N)` prefixes
- 500ms delay between segments ensures delivery order
- Maximum 10 segments per response (configurable)

## Custom Domain Setup

The SMS gateway is accessible at `api.schoopet.com`. To set up a custom domain for Cloud Run:

### 1. Create Domain Mapping

```bash
gcloud beta run domain-mappings create \
  --service schoopet-sms-gateway \
  --domain <your-domain> \
  --region <GOOGLE_CLOUD_LOCATION> \
  --project <GOOGLE_CLOUD_PROJECT>
```

### 2. Configure DNS

Add a CNAME record in your DNS provider:

| Type  | Name | Value                 |
|-------|------|-----------------------|
| CNAME | api  | ghs.googlehosted.com. |

### 3. Verify Domain Mapping

Check the status of the domain mapping:

```bash
gcloud beta run domain-mappings describe \
  --domain <your-domain> \
  --region <GOOGLE_CLOUD_LOCATION> \
  --project <GOOGLE_CLOUD_PROJECT>
```

SSL certificates are provisioned automatically by Google once DNS is configured correctly.

### 4. Update Twilio Webhook

After the domain is active, update the Twilio webhook URL to:
```
https://api.schoopet.com/webhook/sms
```

## Security

- Twilio webhook signatures are validated
- Credentials stored in Secret Manager
- Cloud Run handles HTTPS termination
