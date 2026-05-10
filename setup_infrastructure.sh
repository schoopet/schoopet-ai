#!/bin/bash
# Setup Schoopet Infrastructure
#
# Enables APIs, creates Service Accounts, grants IAM roles, and sets up Cloud Tasks.

set -e

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-mmontan-ml}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
QUEUE_NAME="async-agent-tasks"

echo "=========================================="
echo "Setting up Schoopet Infrastructure"
echo "=========================================="
echo "Project: $PROJECT_ID"
echo "Region: $REGION"
echo "=========================================="

# 1. Enable APIs
echo ""
echo "Enabling required APIs..."
gcloud services enable \
    cloudtasks.googleapis.com \
    run.googleapis.com \
    firestore.googleapis.com \
    aiplatform.googleapis.com \
    cloudbuild.googleapis.com \
    --project "$PROJECT_ID"

# 2. Create Service Accounts
echo ""
echo "Creating Service Accounts..."

GATEWAY_SA_NAME="schoopet-sms-gateway"
GATEWAY_SA_EMAIL="$GATEWAY_SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"

if ! gcloud iam service-accounts describe "$GATEWAY_SA_EMAIL" --project "$PROJECT_ID" >/dev/null 2>&1; then
    gcloud iam service-accounts create "$GATEWAY_SA_NAME" \
        --display-name "Schoopet SMS Gateway" \
        --project "$PROJECT_ID"
    echo "Created $GATEWAY_SA_EMAIL"
    echo "Waiting for propagation..."
    sleep 15
else
    echo "$GATEWAY_SA_EMAIL already exists"
fi

# 3. Grant IAM Roles
echo ""
echo "Granting IAM Roles..."

roles=(
    "roles/datastore.user"
    "roles/aiplatform.user"
    "roles/cloudtasks.enqueuer"
    "roles/cloudtasks.viewer"
    "roles/iam.serviceAccountTokenCreator"
    "roles/run.invoker"
)

for role in "${roles[@]}"; do
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="serviceAccount:$GATEWAY_SA_EMAIL" \
        --role="$role" \
        --condition=None \
        --quiet >/dev/null
    echo "Granted $role to $GATEWAY_SA_EMAIL"
done

# Grant Default Compute SA (used by Agents) permissions:
# - Cloud Tasks Enqueuer (to schedule tasks)
# - Service Account User (to act as itself)
# - Run Invoker (to call SMS Gateway)

# Agent Identity SA
AGENT_SA_NAME="schoopet-agent-identity"
AGENT_SA_EMAIL="$AGENT_SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"

if ! gcloud iam service-accounts describe "$AGENT_SA_EMAIL" --project "$PROJECT_ID" >/dev/null 2>&1; then
    gcloud iam service-accounts create "$AGENT_SA_NAME" \
        --display-name "Schoopet Agent Identity" \
        --project "$PROJECT_ID"
    echo "Created $AGENT_SA_EMAIL"
    echo "Waiting for propagation..."
    sleep 15
else
    echo "$AGENT_SA_EMAIL already exists"
fi

echo "Configuring Agent Identity ($AGENT_SA_EMAIL)..."

agent_roles=(
    "roles/cloudtasks.enqueuer"
    "roles/run.invoker"
    "roles/aiplatform.user"
    "roles/logging.logWriter"
    "roles/datastore.user"
    "roles/serviceusage.serviceUsageConsumer"
)

for role in "${agent_roles[@]}"; do
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="serviceAccount:$AGENT_SA_EMAIL" \
        --role="$role" \
        --condition=None \
        --quiet >/dev/null
    echo "Granted $role to $AGENT_SA_EMAIL"
done

# Grant Agent Identity SA permission to create tokens for SMS Gateway SA
echo "Granting token creator permission on SMS Gateway SA..."
gcloud iam service-accounts add-iam-policy-binding "$GATEWAY_SA_EMAIL" \
    --member="serviceAccount:$AGENT_SA_EMAIL" \
    --role="roles/iam.serviceAccountTokenCreator" \
    --project="$PROJECT_ID" \
    --quiet >/dev/null
echo "Granted iam.serviceAccountTokenCreator on $GATEWAY_SA_EMAIL to $AGENT_SA_EMAIL"

# 4. Create Cloud Tasks Queue
echo ""
echo "Setting up Cloud Tasks..."

if ! gcloud tasks queues describe "$QUEUE_NAME" --location "$REGION" --project "$PROJECT_ID" >/dev/null 2>&1; then
    gcloud tasks queues create "$QUEUE_NAME" \
        --location "$REGION" \
        --project "$PROJECT_ID"
    echo "Created queue: $QUEUE_NAME"
else
    echo "Queue $QUEUE_NAME already exists"
fi

echo ""
echo "=========================================="
echo "Infrastructure Setup Complete!"
echo "=========================================="
echo "SMS Gateway SA: $GATEWAY_SA_EMAIL"
echo "Queue: projects/$PROJECT_ID/locations/$REGION/queues/$QUEUE_NAME"
echo ""
echo "=========================================="
echo "IMPORTANT: After deploying the agent with --agent-identity,"
echo "run the following to grant permissions to the Agent Identity principal:"
echo ""
echo "  ./setup_agent_identity_permissions.sh <AGENT_ENGINE_ID>"
echo ""
echo "This grants the agent identity the permissions it needs to:"
echo "  - Create Cloud Tasks (cloudtasks.enqueuer)"
echo "  - Invoke Cloud Run services (run.invoker)"
echo "  - Access Firestore (datastore.user)"
echo "  - Use project services (serviceusage.serviceUsageConsumer)"
echo "  - Create OIDC tokens for SMS gateway SA"
echo "=========================================="
