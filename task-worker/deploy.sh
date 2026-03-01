#!/bin/bash
# Deploy Task Worker to Cloud Run
#
# Usage:
#   ./task-worker/deploy.sh                      # Deploy to dev (default)
#   ./task-worker/deploy.sh --env=wib-boss-finder # Deploy to wib-boss-finder environment
#   ./task-worker/deploy.sh --env=prod           # Deploy to prod environment
#
# Prerequisites:
#   - gcloud CLI installed and authenticated
#   - environments/<name>.env with GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_AGENT_ENGINE_ID,
#     SMS_GATEWAY_URL set

set -e

# Get script and repo root directories
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

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
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
fi

# Configuration
PROJECT_ID="${GOOGLE_CLOUD_PROJECT}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
SERVICE_NAME="schoopet-task-worker"
SERVICE_ACCOUNT="${TASK_WORKER_SA:-task-worker@${PROJECT_ID}.iam.gserviceaccount.com}"
AGENT_ENGINE_ID="${AGENT_ENGINE_ID:-${GOOGLE_CLOUD_AGENT_ENGINE_ID}}"

# Validate required variables
if [ -z "$PROJECT_ID" ]; then
    echo "Error: GOOGLE_CLOUD_PROJECT is not set. Check environments/${ENV_NAME}.env"
    exit 1
fi

if [ -z "$AGENT_ENGINE_ID" ]; then
    echo "Error: AGENT_ENGINE_ID or GOOGLE_CLOUD_AGENT_ENGINE_ID is required"
    echo "Set it in environments/${ENV_NAME}.env"
    exit 1
fi

if [ -z "$SMS_GATEWAY_URL" ]; then
    echo "Error: SMS_GATEWAY_URL is required"
    echo "Set it in environments/${ENV_NAME}.env"
    exit 1
fi

echo "=========================================="
echo "Deploying Schoopet Task Worker"
echo "=========================================="
echo "Environment:     $ENV_NAME"
echo "Project:         $PROJECT_ID"
echo "Region:          $REGION"
echo "Service:         $SERVICE_NAME"
echo "Service Account: $SERVICE_ACCOUNT"
echo "Agent Engine:    $AGENT_ENGINE_ID"
echo "SMS Gateway:     $SMS_GATEWAY_URL"
echo "=========================================="

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
