#!/bin/bash
# Deploy SMS Gateway to Cloud Run
#
# Usage:
#   ./scripts/deploy.sh
#
# Prerequisites:
#   - gcloud CLI installed and authenticated
#   - Secrets created in Secret Manager (run setup_secrets.sh first)
#   - GOOGLE_CLOUD_PROJECT and AGENT_ENGINE_ID environment variables set

set -e

# Configuration
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-mmontan-ml}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
SERVICE_NAME="shoopet-sms-gateway"
AGENT_ENGINE_ID="${AGENT_ENGINE_ID}"

# Validate required variables
if [ -z "$AGENT_ENGINE_ID" ]; then
    echo "Error: AGENT_ENGINE_ID environment variable is required"
    echo "Set it with: export AGENT_ENGINE_ID=<your-agent-engine-id>"
    exit 1
fi

echo "=========================================="
echo "Deploying Schoopet SMS Gateway"
echo "=========================================="
echo "Project: $PROJECT_ID"
echo "Region: $REGION"
echo "Service: $SERVICE_NAME"
echo "Agent Engine: $AGENT_ENGINE_ID"
echo "=========================================="

# Get the directory of this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

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
    --set-env-vars "GOOGLE_CLOUD_PROJECT=$PROJECT_ID,GOOGLE_CLOUD_LOCATION=$REGION,AGENT_ENGINE_ID=$AGENT_ENGINE_ID,GOOGLE_OAUTH_REDIRECT_URI=https://api.schoopet.com/oauth/google/callback" \
    --set-secrets "TWILIO_ACCOUNT_SID=twilio-account-sid:latest,TWILIO_AUTH_TOKEN=twilio-auth-token:latest,TWILIO_PHONE_NUMBER=twilio-phone-number:latest,TWILIO_WHATSAPP_NUMBER=twilio-whatsapp-number:latest,GOOGLE_OAUTH_CLIENT_ID=google-oauth-client-id:latest,GOOGLE_OAUTH_CLIENT_SECRET=google-oauth-client-secret:latest,TELEGRAM_BOT_TOKEN=telegram-bot-token:latest"

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
