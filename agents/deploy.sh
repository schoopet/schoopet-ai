#!/bin/bash
# Deploy Schoopet Agent to Vertex AI Agent Engine
#
# Usage:
#   ./deploy.sh --agent-type=personal                      # Deploy personal agent to dev (default)
#   ./deploy.sh --agent-type=team --env=wib-boss-finder    # Deploy team agent to wib-boss-finder
#   ./deploy.sh --agent-type=personal --env=prod           # Deploy personal agent to prod
#   ./deploy.sh --agent-type=team --new                    # Create a new team agent engine
#   ./deploy.sh --agent-type=personal --identity           # Deploy with Agent Identity enabled
#
# Environment variables (loaded from environments/<name>.env, then schoopet/.env):
#   GOOGLE_CLOUD_PROJECT       - GCP project ID (required)
#   GOOGLE_CLOUD_LOCATION      - GCP region (default: us-central1)
#   PERSONAL_AGENT_ENGINE_ID   - Existing personal agent engine ID for updates
#   TEAM_AGENT_ENGINE_ID       - Existing team agent engine ID for updates

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

# Pre-parse --env and --agent-type flags (before loading env files)
ENV_NAME="dev"
AGENT_TYPE=""
for arg in "$@"; do
    case $arg in
        --env=*) ENV_NAME="${arg#*=}" ;;
        --agent-type=*) AGENT_TYPE="${arg#*=}" ;;
    esac
done

# Validate agent type early
if [ -z "$AGENT_TYPE" ]; then
    echo -e "${RED}Error: --agent-type=personal|team is required${NC}"
    echo "Usage: ./deploy.sh --agent-type=personal|team [--env=<name>] [--new] [--identity]"
    exit 1
fi
if [ "$AGENT_TYPE" != "personal" ] && [ "$AGENT_TYPE" != "team" ]; then
    echo -e "${RED}Error: --agent-type must be 'personal' or 'team', got '${AGENT_TYPE}'${NC}"
    exit 1
fi

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

# Load component-level .env for secrets / local overrides (second, can override)
if [ -f "schoopet/.env" ]; then
    echo -e "${GREEN}Loading secrets from schoopet/.env${NC}"
    set -a
    source schoopet/.env
    set +a
else
    echo -e "${YELLOW}Warning: schoopet/.env not found, using environment variables${NC}"
fi

# Parse all arguments
NEW_DEPLOY=false
USE_IDENTITY=false
CLI_ID=""

while [[ $# -gt 0 ]]; do
    case $1 in
        --env=*)
            shift
            ;;
        --agent-type=*)
            shift
            ;;
        --new)
            NEW_DEPLOY=true
            shift
            ;;
        --identity)
            USE_IDENTITY=true
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

# Determine engine ID for this agent type
if [ "$AGENT_TYPE" = "personal" ]; then
    ENV_ENGINE_ID="${PERSONAL_AGENT_ENGINE_ID:-}"
else
    ENV_ENGINE_ID="${TEAM_AGENT_ENGINE_ID:-}"
fi

# CLI --id overrides env var
ENGINE_ID="${CLI_ID:-$ENV_ENGINE_ID}"

echo ""
echo -e "Environment: ${GREEN}${ENV_NAME}${NC}"
echo -e "Agent type:  ${GREEN}${AGENT_TYPE}${NC}"
echo -e "Project:     ${GREEN}$GOOGLE_CLOUD_PROJECT${NC}"
echo -e "Location:    ${GREEN}$GOOGLE_CLOUD_LOCATION${NC}"

# Activate virtual environment
VENV_PATH="$SCRIPT_DIR/schoopet/.venv"
echo -e "${GREEN}Activating virtual environment: $VENV_PATH${NC}"
source "$VENV_PATH/bin/activate"
PYTHON_CMD="$VENV_PATH/bin/python"

# Build deployment command
DEPLOY_CMD="$PYTHON_CMD -m schoopet.deploy"
DEPLOY_CMD="$DEPLOY_CMD --project $GOOGLE_CLOUD_PROJECT"
DEPLOY_CMD="$DEPLOY_CMD --location $GOOGLE_CLOUD_LOCATION"
DEPLOY_CMD="$DEPLOY_CMD --agent-type $AGENT_TYPE"

if [ "$NEW_DEPLOY" = true ]; then
    echo -e "Mode:        ${YELLOW}Creating NEW agent engine${NC}"
elif [ -n "$ENGINE_ID" ]; then
    echo -e "Engine:      ${GREEN}$ENGINE_ID${NC}"
    echo -e "Mode:        ${GREEN}Updating existing agent${NC}"
    DEPLOY_CMD="$DEPLOY_CMD --id $ENGINE_ID"
else
    echo -e "${YELLOW}Warning: No engine ID set for ${AGENT_TYPE} agent, creating new engine${NC}"
fi

if [ "$USE_IDENTITY" = true ]; then
    echo -e "Identity:    ${GREEN}Agent Identity enabled${NC}"
    DEPLOY_CMD="$DEPLOY_CMD --agent-identity"
fi

echo ""
echo -e "${BLUE}Running deployment...${NC}"
echo ""

# Execute deployment
$DEPLOY_CMD

echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  Deployment Complete${NC}"
echo -e "${GREEN}========================================${NC}"
