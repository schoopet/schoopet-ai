#!/bin/bash
# Deploy SMS Gateway to Cloud Run
#
# Usage:
#   ./scripts/deploy.sh                      # Deploy to dev (default)
#   ./scripts/deploy.sh --env=wib-boss-finder # Deploy to wib-boss-finder environment
#   ./scripts/deploy.sh --env=prod           # Deploy to prod environment
#
# Prerequisites:
#   - gcloud CLI installed and authenticated
#   - Secrets created in Secret Manager (run setup_secrets.sh first)
#   - environments/<name>.env with GOOGLE_CLOUD_PROJECT and GOOGLE_CLOUD_AGENT_ENGINE_ID set

set -e

# Get script and repo root directories
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
REPO_ROOT="$(dirname "$PROJECT_ROOT")"

# Pre-parse --env flag
ENV_NAME="dev"
for arg in "$@"; do
    case $arg in
        --env=*) ENV_NAME="${arg#*=}" ;;
    esac
done

# Load environment-specific settings
ENV_FILE="$REPO_ROOT/environments/${ENV_NAME}.env"
if [ ! -f "$ENV_FILE" ]; then
    echo "Error: environments/${ENV_NAME}.env not found"
    exit 1
fi
echo "Loading environment: ${ENV_NAME}"
set -a
source "$ENV_FILE"
set +a

# Load component-level .env for secrets / local overrides
if [ -f "$PROJECT_ROOT/.env" ]; then
    set -a
    source "$PROJECT_ROOT/.env"
    set +a
fi

# Configuration
PROJECT_ID="${GOOGLE_CLOUD_PROJECT}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
SERVICE_NAME="shoopet-sms-gateway"
AGENT_ENGINE_ID="${AGENT_ENGINE_ID:-${GOOGLE_CLOUD_AGENT_ENGINE_ID}}"
EMAIL_PUBSUB_TOPIC="${EMAIL_PUBSUB_TOPIC:-projects/${PROJECT_ID}/topics/email-notifications}"

# Validate required variables
if [ -z "$PROJECT_ID" ]; then
    echo "Error: GOOGLE_CLOUD_PROJECT is not set. Check environments/${ENV_NAME}.env"
    exit 1
fi

if [ -z "$AGENT_ENGINE_ID" ]; then
    echo "Error: AGENT_ENGINE_ID or GOOGLE_CLOUD_AGENT_ENGINE_ID environment variable is required"
    echo "Set it in environments/${ENV_NAME}.env"
    exit 1
fi

echo "=========================================="
echo "Deploying Schoopet SMS Gateway"
echo "=========================================="
echo "Environment:  $ENV_NAME"
echo "Project:      $PROJECT_ID"
echo "Region:       $REGION"
echo "Service:      $SERVICE_NAME"
echo "Agent Engine: $AGENT_ENGINE_ID"
echo "=========================================="

# Change to project root
cd "$PROJECT_ROOT"

# Build and push container image
echo ""
echo "Building container image..."
gcloud builds submit \
    --tag "gcr.io/$PROJECT_ID/$SERVICE_NAME" \
    --project "$PROJECT_ID"

# Deploy to Cloud Run
echo ""
echo "Deploying to Cloud Run..."
gcloud run deploy "$SERVICE_NAME" \
    --image "gcr.io/$PROJECT_ID/$SERVICE_NAME" \
    --platform managed \
    --region "$REGION" \
    --project "$PROJECT_ID" \
    --allow-unauthenticated \
    --min-instances 1 \
    --max-instances 10 \
    --memory 512Mi \
    --cpu 1 \
    --timeout 300 \
    --set-env-vars "GOOGLE_CLOUD_PROJECT=$PROJECT_ID,GOOGLE_CLOUD_LOCATION=$REGION,AGENT_ENGINE_ID=$AGENT_ENGINE_ID,GOOGLE_OAUTH_REDIRECT_URI=${OAUTH_BASE_URL:-https://api.schoopet.com}/oauth/google/callback,EMAIL_DRIVE_FOLDER_ID=${EMAIL_DRIVE_FOLDER_ID:-},EMAIL_SHEET_ID=${EMAIL_SHEET_ID:-},EMAIL_PUBSUB_TOPIC=${EMAIL_PUBSUB_TOPIC},ARTIFACT_BUCKET_NAME=${ARTIFACT_BUCKET_NAME:-}" \
    --set-secrets "TWILIO_ACCOUNT_SID=twilio-account-sid:latest,TWILIO_AUTH_TOKEN=twilio-auth-token:latest,TWILIO_PHONE_NUMBER=twilio-phone-number:latest,TWILIO_WHATSAPP_NUMBER=twilio-whatsapp-number:latest,GOOGLE_OAUTH_CLIENT_ID=google-oauth-client-id:latest,GOOGLE_OAUTH_CLIENT_SECRET=google-oauth-client-secret:latest,TELEGRAM_BOT_TOKEN=telegram-bot-token:latest,SLACK_BOT_TOKEN=slack-bot-token:latest,SLACK_SIGNING_SECRET=slack-signing-secret:latest"

# Get the service URL
echo ""
echo "Getting service URL..."
SERVICE_URL=$(gcloud run services describe "$SERVICE_NAME" \
    --region="$REGION" \
    --project="$PROJECT_ID" \
    --format='value(status.url)')

echo ""
echo "=========================================="
echo "Deployment Complete!"
echo "=========================================="
echo "Service URL: $SERVICE_URL"
echo ""
echo "Configure Twilio webhook to:"
echo "  URL: ${SERVICE_URL}/webhook/sms"
echo "  Method: HTTP POST"
echo ""
echo "Test the health endpoint:"
echo "  curl ${SERVICE_URL}/health"
echo "=========================================="
