#!/bin/bash
# Provision the Google Personal IAM connector for agent-identity 3LO auth.
#
# The google_iam_connector Terraform resource is not yet supported by the
# google-beta provider (Pre-GA). Run this script once per environment to
# create the connector manually. Re-running is idempotent — it skips creation
# if the connector already exists.
#
# Usage:
#   ./scripts/provision_iam_connector.sh              # dev (default)
#   ./scripts/provision_iam_connector.sh --env=prod

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

# Parse --env flag
ENV_NAME="dev"
for arg in "$@"; do
    case $arg in
        --env=*) ENV_NAME="${arg#*=}" ;;
    esac
done

# Load environment settings
ENV_FILE="$REPO_ROOT/environments/${ENV_NAME}.env"
if [ ! -f "$ENV_FILE" ]; then
    echo "Error: environments/${ENV_NAME}.env not found"
    exit 1
fi
set -a
source "$ENV_FILE"
set +a

SECRETS_FILE="$REPO_ROOT/environments/${ENV_NAME}.secrets.env"
if [ -f "$SECRETS_FILE" ]; then
    set -a
    source "$SECRETS_FILE"
    set +a
fi

PROJECT_ID="${GOOGLE_CLOUD_PROJECT}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
CONNECTOR_ID="google-personal-${ENV_NAME}"
ENGINE_ID="${PERSONAL_AGENT_ENGINE_ID}"

if [ -z "$PROJECT_ID" ]; then
    echo "Error: GOOGLE_CLOUD_PROJECT not set"
    exit 1
fi
if [ -z "$GOOGLE_OAUTH_CLIENT_ID" ] || [ -z "$GOOGLE_OAUTH_CLIENT_SECRET" ]; then
    echo "Error: GOOGLE_OAUTH_CLIENT_ID and GOOGLE_OAUTH_CLIENT_SECRET must be set in environments/${ENV_NAME}.secrets.env"
    exit 1
fi
if [ -z "$ENGINE_ID" ]; then
    echo "Error: PERSONAL_AGENT_ENGINE_ID not set"
    exit 1
fi

echo "=========================================="
echo "Provisioning IAM Connector"
echo "=========================================="
echo "Environment: $ENV_NAME"
echo "Project:     $PROJECT_ID"
echo "Region:      $REGION"
echo "Connector:   $CONNECTOR_ID"
echo "=========================================="

# ── 1. Enable APIs ────────────────────────────────────────────────────────────

echo ""
echo "Enabling APIs..."
gcloud services enable iamconnectors.googleapis.com --project="$PROJECT_ID" --quiet
gcloud services enable iamconnectorcredentials.googleapis.com --project="$PROJECT_ID" --quiet
echo "APIs enabled."

# ── 2. Create connector (skip if already exists) ─────────────────────────────

echo ""
echo "Checking for existing connector..."
EXISTING=$(gcloud alpha agent-identity connectors describe "$CONNECTOR_ID" \
    --project="$PROJECT_ID" --location="$REGION" --format="value(name)" 2>/dev/null || true)

if [ -n "$EXISTING" ]; then
    echo "Connector already exists: $EXISTING"
else
    echo "Creating connector..."
    gcloud alpha agent-identity connectors create "$CONNECTOR_ID" \
        --project="$PROJECT_ID" \
        --location="$REGION" \
        --three-legged-oauth-authorization-url="https://accounts.google.com/o/oauth2/v2/auth" \
        --three-legged-oauth-token-url="https://oauth2.googleapis.com/token" \
        --three-legged-oauth-client-id="${GOOGLE_OAUTH_CLIENT_ID}" \
        --three-legged-oauth-client-secret="${GOOGLE_OAUTH_CLIENT_SECRET}" \
        --allowed-scopes="https://www.googleapis.com/auth/calendar.events,https://www.googleapis.com/auth/drive,https://www.googleapis.com/auth/documents,https://www.googleapis.com/auth/spreadsheets,https://www.googleapis.com/auth/gmail.readonly,https://www.googleapis.com/auth/gmail.modify,https://www.googleapis.com/auth/userinfo.email,openid"
    echo "Connector created."
fi

CONNECTOR_NAME="projects/${PROJECT_ID}/locations/${REGION}/connectors/${CONNECTOR_ID}"

# ── 3. Grant agent principal roles/iamconnectors.user ────────────────────────

echo ""
echo "Fetching agent engine effective identity..."
TOKEN=$(gcloud auth print-access-token)
EFFECTIVE_IDENTITY=$(curl -s -H "Authorization: Bearer $TOKEN" \
    "https://${REGION}-aiplatform.googleapis.com/v1beta1/projects/${PROJECT_ID}/locations/${REGION}/reasoningEngines/${ENGINE_ID}" \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('spec',{}).get('effectiveIdentity',''))")

if [ -z "$EFFECTIVE_IDENTITY" ]; then
    echo "Error: could not fetch effectiveIdentity for engine $ENGINE_ID"
    exit 1
fi

PRINCIPAL="principal://${EFFECTIVE_IDENTITY}"
echo "Agent principal: $PRINCIPAL"

echo ""
echo "Setting IAM policy (roles/iamconnectors.user)..."
TOKEN=$(gcloud auth print-access-token)
RESPONSE=$(curl -s -X POST \
    -H "Authorization: Bearer $TOKEN" \
    -H "x-goog-user-project: $PROJECT_ID" \
    -H "Content-Type: application/json" \
    "https://iamconnectors.googleapis.com/v1alpha/projects/${PROJECT_ID}/locations/${REGION}/connectors/${CONNECTOR_ID}:setIamPolicy" \
    -d "{
        \"policy\": {
            \"bindings\": [{
                \"role\": \"roles/iamconnectors.user\",
                \"members\": [\"${PRINCIPAL}\"]
            }]
        }
    }")

if echo "$RESPONSE" | grep -q '"etag"'; then
    echo "IAM policy set."
else
    echo "Error setting IAM policy:"
    echo "$RESPONSE"
    exit 1
fi

# ── 4. Summary ────────────────────────────────────────────────────────────────

echo ""
echo "=========================================="
echo "Done!"
echo "=========================================="
echo ""
echo "Connector resource name:"
echo "  $CONNECTOR_NAME"
echo ""
echo "Set this in environments/${ENV_NAME}.env:"
echo "  IAM_CONNECTOR_GOOGLE_PERSONAL_NAME=${CONNECTOR_NAME}"
echo ""
echo "Then redeploy the gateway to apply the env var:"
echo "  ./sms-gateway/scripts/deploy.sh --env=${ENV_NAME}"
