#!/bin/bash
# Grant IAM permissions to Agent Identity Principal
#
# This script grants the necessary permissions to the Agent Identity principal
# that is created when deploying an agent with --agent-identity flag.
#
# Usage:
#   ./setup_agent_identity_permissions.sh <AGENT_ENGINE_ID>
#
# The Agent Identity principal format is:
#   principal://agents.global.proj-<PROJECT_NUM>.system.id.goog/resources/aiplatform/projects/<PROJECT_NUM>/locations/<REGION>/reasoningEngines/<ENGINE_ID>

set -e

if [ -z "$1" ]; then
    echo "Usage: $0 <AGENT_ENGINE_ID>"
    echo "Example: $0 2114234416376053760"
    exit 1
fi

AGENT_ENGINE_ID="$1"
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-mmontan-ml}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
GATEWAY_SA_EMAIL="schoopet-sms-gateway@$PROJECT_ID.iam.gserviceaccount.com"

# Get project number
PROJECT_NUM=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')

AGENT_PRINCIPAL="principal://agents.global.proj-${PROJECT_NUM}.system.id.goog/resources/aiplatform/projects/${PROJECT_NUM}/locations/${REGION}/reasoningEngines/${AGENT_ENGINE_ID}"

echo "=========================================="
echo "Granting permissions to Agent Identity"
echo "=========================================="
echo "Project: $PROJECT_ID (${PROJECT_NUM})"
echo "Region: $REGION"
echo "Agent Engine ID: $AGENT_ENGINE_ID"
echo "Agent Principal: $AGENT_PRINCIPAL"
echo "=========================================="

# Roles to grant to the agent identity
ROLES=(
    "roles/cloudtasks.enqueuer"
    "roles/run.invoker"
    "roles/datastore.user"
    "roles/serviceusage.serviceUsageConsumer"
    "roles/aiplatform.user"
    "roles/secretmanager.secretAccessor"
    "roles/bigquery.dataEditor"
    "roles/bigquery.jobUser"
)

echo ""
echo "Fetching current IAM policy..."

# Get current policy using REST API (v3 supports principal:// format)
POLICY=$(curl -s -X POST \
    -H "Authorization: Bearer $(gcloud auth print-access-token)" \
    -H "Content-Type: application/json" \
    "https://cloudresourcemanager.googleapis.com/v3/projects/${PROJECT_ID}:getIamPolicy" \
    -d '{"options": {"requestedPolicyVersion": 3}}')

echo "Adding agent identity to roles..."

# Add the agent identity to each role
for role in "${ROLES[@]}"; do
    echo "  Adding to $role..."
    POLICY=$(echo "$POLICY" | jq --arg principal "$AGENT_PRINCIPAL" --arg role "$role" '
        .bindings |= (
            if any(.role == $role) then
                map(if .role == $role then .members += [$principal] | .members |= unique else . end)
            else
                . + [{"role": $role, "members": [$principal]}]
            end
        )
    ')
done

echo ""
echo "Setting updated IAM policy..."

# Set the updated policy
RESULT=$(curl -s -X POST \
    -H "Authorization: Bearer $(gcloud auth print-access-token)" \
    -H "Content-Type: application/json" \
    "https://cloudresourcemanager.googleapis.com/v3/projects/${PROJECT_ID}:setIamPolicy" \
    -d "{\"policy\": $POLICY}")

# Check for errors
if echo "$RESULT" | jq -e '.error' >/dev/null 2>&1; then
    echo "ERROR: Failed to set IAM policy"
    echo "$RESULT" | jq '.error'
    exit 1
fi

echo "Project-level permissions granted successfully."

echo ""
echo "Granting token creator permission on SMS Gateway SA..."

# Grant iam.serviceAccountTokenCreator on SMS Gateway SA
# This allows the agent to create OIDC tokens for Cloud Tasks
gcloud iam service-accounts add-iam-policy-binding "$GATEWAY_SA_EMAIL" \
    --member="$AGENT_PRINCIPAL" \
    --role="roles/iam.serviceAccountTokenCreator" \
    --project="$PROJECT_ID" \
    --quiet >/dev/null

echo "Granted iam.serviceAccountTokenCreator on $GATEWAY_SA_EMAIL"

# Grant iam.serviceAccountUser on SMS Gateway SA
# This allows the agent to "act as" the SMS gateway SA when creating Cloud Tasks
gcloud iam service-accounts add-iam-policy-binding "$GATEWAY_SA_EMAIL" \
    --member="$AGENT_PRINCIPAL" \
    --role="roles/iam.serviceAccountUser" \
    --project="$PROJECT_ID" \
    --quiet >/dev/null

echo "Granted iam.serviceAccountUser on $GATEWAY_SA_EMAIL"

echo ""
echo "=========================================="
echo "Agent Identity Permissions Complete!"
echo "=========================================="
echo ""
echo "The agent identity now has permissions to:"
echo "  - Create Cloud Tasks (for async task scheduling)"
echo "  - Invoke Cloud Run services (sms-gateway)"
echo "  - Access Firestore (task state storage)"
echo "  - Use project services (API access)"
echo "  - Create OIDC tokens for SMS gateway SA"
echo ""
echo "Note: Permissions may take 1-2 minutes to propagate."
echo "=========================================="
