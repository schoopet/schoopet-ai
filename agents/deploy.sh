#!/bin/bash
# Deploy Schoopet Agent to Vertex AI Agent Engine
#
# Updates an existing agent engine. New engines must be created via Terraform.
#
# Usage:
#   ./deploy.sh                        # Deploy to dev (default)
#   ./deploy.sh --env=prod             # Deploy to prod environment
#   ./deploy.sh --from-tf              # Read engine ID from terraform output
#
# Environment variables (loaded from environments/<name>.env, then schoopet/.env):
#   GOOGLE_CLOUD_PROJECT       - GCP project ID (required)
#   GOOGLE_CLOUD_LOCATION      - GCP region (default: us-central1)
#   PERSONAL_AGENT_ENGINE_ID   - Agent engine ID to update (required)

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}  Schoopet Agent Deployment${NC}"
echo -e "${BLUE}========================================${NC}"

# Pre-parse --env flag (before loading env files)
ENV_NAME="dev"
for arg in "$@"; do
    case $arg in
        --env=*) ENV_NAME="${arg#*=}" ;;
    esac
done

# Load environment-specific settings (project-level, not secret)
ENV_FILE="$SCRIPT_DIR/../environments/${ENV_NAME}.env"
if [ ! -f "$ENV_FILE" ]; then
    echo -e "${RED}Error: environments/${ENV_NAME}.env not found${NC}"
    exit 1
fi
echo -e "${GREEN}Loading environment: ${ENV_NAME}${NC}"
set -a
source "$ENV_FILE"
set +a

# Load per-environment secrets (second, can override)
SECRETS_FILE="$SCRIPT_DIR/../environments/${ENV_NAME}.secrets.env"
if [ -f "$SECRETS_FILE" ]; then
    echo -e "${GREEN}Loading secrets from environments/${ENV_NAME}.secrets.env${NC}"
    set -a
    source "$SECRETS_FILE"
    set +a
else
    echo -e "${YELLOW}Warning: environments/${ENV_NAME}.secrets.env not found, using environment variables${NC}"
fi

# Parse all arguments
CLI_ID=""
FROM_TF=false

while [[ $# -gt 0 ]]; do
    case $1 in
        --env=*)
            shift
            ;;
        --from-tf)
            FROM_TF=true
            shift
            ;;
        --project)
            GOOGLE_CLOUD_PROJECT="$2"
            shift 2
            ;;
        --location)
            GOOGLE_CLOUD_LOCATION="$2"
            shift 2
            ;;
        --id)
            CLI_ID="$2"
            shift 2
            ;;
        *)
            echo -e "${RED}Unknown option: $1${NC}"
            exit 1
            ;;
    esac
done

# Validate required environment variables
if [ -z "$GOOGLE_CLOUD_PROJECT" ]; then
    echo -e "${RED}Error: GOOGLE_CLOUD_PROJECT is not set${NC}"
    echo "Set it in environments/${ENV_NAME}.env or pass --project <project-id>"
    exit 1
fi

# Set defaults
GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"

# Determine engine ID
ENV_ENGINE_ID="${PERSONAL_AGENT_ENGINE_ID:-}"

# CLI --id overrides env var; --from-tf reads resource_name from terraform outputs
ENGINE_ID="${CLI_ID:-$ENV_ENGINE_ID}"

if [ "$FROM_TF" = true ] || [ -z "$ENGINE_ID" ]; then
    TF_DIR="$SCRIPT_DIR/../terraform"
    if command -v terraform &>/dev/null && [ -d "$TF_DIR" ]; then
        RESOURCE_NAME=$(cd "$TF_DIR" && terraform output -raw personal_agent_resource_name 2>/dev/null || true)
        if [ -n "$RESOURCE_NAME" ]; then
            ENGINE_ID="${RESOURCE_NAME##*/}"
            echo -e "Engine ID from Terraform: ${GREEN}$ENGINE_ID${NC}"
        fi
    fi
fi

echo ""
echo -e "Environment: ${GREEN}${ENV_NAME}${NC}"
echo -e "Project:     ${GREEN}$GOOGLE_CLOUD_PROJECT${NC}"
echo -e "Location:    ${GREEN}$GOOGLE_CLOUD_LOCATION${NC}"

# Activate virtual environment
VENV_PATH="$SCRIPT_DIR/.venv"
echo -e "${GREEN}Activating virtual environment: $VENV_PATH${NC}"
source "$VENV_PATH/bin/activate"
PYTHON_CMD="$VENV_PATH/bin/python"

PYTHON_VERSION="$($PYTHON_CMD -c 'import platform; print(platform.python_version())')"
case "$PYTHON_VERSION" in
    3.13.*)
        ;;
    *)
        echo -e "${RED}Error: deployment venv uses Python $PYTHON_VERSION, but Agent Engine requires Python 3.13.${NC}"
        echo "Recreate $VENV_PATH with Python 3.13 before deploying."
        exit 1
        ;;
esac

# Build deployment command
DEPLOY_CMD="$PYTHON_CMD -m schoopet.deploy"
DEPLOY_CMD="$DEPLOY_CMD --project $GOOGLE_CLOUD_PROJECT"
DEPLOY_CMD="$DEPLOY_CMD --location $GOOGLE_CLOUD_LOCATION"

if [ -n "$ENGINE_ID" ]; then
    echo -e "Engine:      ${GREEN}$ENGINE_ID${NC}"
    DEPLOY_CMD="$DEPLOY_CMD --id $ENGINE_ID"
fi

echo -e "Identity:    ${GREEN}Agent Identity enabled${NC}"

echo ""
echo -e "${BLUE}Running deployment...${NC}"
echo ""

# Execute deployment
$DEPLOY_CMD

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Deployment Complete${NC}"
echo -e "${GREEN}========================================${NC}"
