#!/bin/bash
# Deploy Task Worker to Cloud Run
#
# Usage:
#   ./task-worker/deploy.sh
#
# Prerequisites:
#   - gcloud CLI installed and authenticated
#   - GOOGLE_CLOUD_PROJECT, AGENT_ENGINE_ID, SMS_GATEWAY_URL environment variables set

set -e

# Configuration
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-mmontan-ml}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
SERVICE_NAME="schoopet-task-worker"
SERVICE_ACCOUNT="task-worker@${PROJECT_ID}.iam.gserviceaccount.com"

# Validate required variables
if [ -z "$AGENT_ENGINE_ID" ]; then
    echo "Error: AGENT_ENGINE_ID environment variable is required"
    exit 1
fi

if [ -z "$SMS_GATEWAY_URL" ]; then
    echo "Error: SMS_GATEWAY_URL environment variable is required"
    exit 1
fi

echo "=========================================="
echo "Deploying Schoopet Task Worker"
echo "=========================================="
echo "Project: $PROJECT_ID"
echo "Region: $REGION"
echo "Service: $SERVICE_NAME"
echo "Service Account: $SERVICE_ACCOUNT"
echo "Agent Engine: $AGENT_ENGINE_ID"
echo "SMS Gateway: $SMS_GATEWAY_URL"
echo "=========================================="

# Get the directory of this script
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Change to task-worker directory
cd "$SCRIPT_DIR"

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
    --no-allow-unauthenticated \
    --service-account "$SERVICE_ACCOUNT" \
    --min-instances 0 \
    --max-instances 10 \
    --memory 1Gi \
    --cpu 1 \
    --timeout 900 \
    --set-env-vars "GOOGLE_CLOUD_PROJECT=$PROJECT_ID,GOOGLE_CLOUD_LOCATION=$REGION,AGENT_ENGINE_ID=$AGENT_ENGINE_ID,SMS_GATEWAY_URL=$SMS_GATEWAY_URL"

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
echo "Update your agent environment with:"
echo "  TASK_WORKER_URL=$SERVICE_URL"
echo "  TASK_WORKER_SA=$SERVICE_ACCOUNT"
echo ""
echo "Test the health endpoint:"
echo "  curl -H \"Authorization: Bearer \$(gcloud auth print-identity-token)\" ${SERVICE_URL}/health"
echo "=========================================="
