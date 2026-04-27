#!/bin/bash
# Deploy Task Worker to Cloud Run via Terraform
#
# Usage:
#   ./task-worker/deploy.sh                      # Deploy to dev (default)
#   ./task-worker/deploy.sh --env=prod           # Deploy to prod environment
#
# Prerequisites:
#   - gcloud CLI installed and authenticated
#   - Terraform initialized for the target environment
#   - environments/<name>.env with GOOGLE_CLOUD_PROJECT set

set -e

# Get script and repo root directories
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
TF_DIR="$REPO_ROOT/terraform"

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

PROJECT_ID="${GOOGLE_CLOUD_PROJECT}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
SERVICE_NAME="schoopet-task-worker"
IMAGE="us-docker.pkg.dev/$PROJECT_ID/schoopet/$SERVICE_NAME"

if [ -z "$PROJECT_ID" ]; then
    echo "Error: GOOGLE_CLOUD_PROJECT is not set. Check environments/${ENV_NAME}.env"
    exit 1
fi

echo "=========================================="
echo "Deploying Schoopet Task Worker"
echo "=========================================="
echo "Environment: $ENV_NAME"
echo "Project:     $PROJECT_ID"
echo "Region:      $REGION"
echo "Image:       $IMAGE"
echo "=========================================="

# Build and push container image
echo ""
echo "Building container image..."
cd "$SCRIPT_DIR"
gcloud builds submit \
    --tag "$IMAGE" \
    --project "$PROJECT_ID"

echo ""
echo "Image pushed: $IMAGE"

# Resolve the exact digest so Terraform always sees a changed image value
# and creates a new Cloud Run revision — avoids the :latest staleness problem.
echo "Resolving image digest..."
DIGEST=$(gcloud artifacts docker images describe "$IMAGE" \
    --project "$PROJECT_ID" \
    --format="value(image_summary.digest)" 2>/dev/null)
if [ -z "$DIGEST" ]; then
    echo "Warning: could not resolve image digest, falling back to tag URL"
    IMAGE_REF="$IMAGE"
else
    IMAGE_REF="${IMAGE}@${DIGEST}"
    echo "Image digest: $IMAGE_REF"
fi

# Deploy via Terraform
echo ""
echo "Applying Terraform..."
TFVARS_FILE="$TF_DIR/environments/${ENV_NAME}.tfvars"
if [ ! -f "$TFVARS_FILE" ]; then
    echo "Warning: $TFVARS_FILE not found, running terraform without -var-file"
    (cd "$TF_DIR" && terraform apply -auto-approve -var="task_worker_image=$IMAGE_REF")
else
    (cd "$TF_DIR" && terraform apply -auto-approve -var-file="$TFVARS_FILE" -var="task_worker_image=$IMAGE_REF")
fi

echo ""
echo "=========================================="
echo "Deployment Complete!"
echo "=========================================="
TASK_WORKER_URL=$(cd "$TF_DIR" && terraform output -raw task_worker_url 2>/dev/null || echo "(unknown)")
echo "Task Worker URL: $TASK_WORKER_URL"
echo ""
echo "Update environments/${ENV_NAME}.env with:"
echo "  TASK_WORKER_URL=$TASK_WORKER_URL"
echo ""
echo "Test the health endpoint:"
echo "  curl -H \"Authorization: Bearer \$(gcloud auth print-identity-token)\" ${TASK_WORKER_URL}/health"
echo "=========================================="
