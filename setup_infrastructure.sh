#!/bin/bash
# Setup Shoopet Infrastructure
#
# Enables APIs, creates Service Accounts, grants IAM roles, and sets up Cloud Tasks.

set -e

PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-mmontan-ml}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
QUEUE_NAME="async-agent-tasks"

echo "=========================================="
echo "Setting up Shoopet Infrastructure"
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

# Task Worker SA
WORKER_SA_NAME="task-worker"
WORKER_SA_EMAIL="$WORKER_SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"

if ! gcloud iam service-accounts describe "$WORKER_SA_EMAIL" --project "$PROJECT_ID" >/dev/null 2>&1; then
    gcloud iam service-accounts create "$WORKER_SA_NAME" \
        --display-name "Shoopet Task Worker" \
        --project "$PROJECT_ID"
    echo "Created $WORKER_SA_EMAIL"
    echo "Waiting for propagation..."
    sleep 15
else
    echo "$WORKER_SA_EMAIL already exists"
fi

# 3. Grant IAM Roles
echo ""
echo "Granting IAM Roles..."

# Grant Task Worker permissions:
# - Firestore User (read/write tasks)
# - Vertex AI User (run async agents)
# - Service Account Token Creator (for OIDC to SMS Gateway)
# - Run Invoker (to call SMS Gateway - if internal auth uses IAM)

roles=(
    "roles/datastore.user"
    "roles/aiplatform.user"
    "roles/iam.serviceAccountTokenCreator"
    "roles/run.invoker"
)

for role in "${roles[@]}"; do
    gcloud projects add-iam-policy-binding "$PROJECT_ID" \
        --member="serviceAccount:$WORKER_SA_EMAIL" \
        --role="$role" \
        --condition=None \
        --quiet >/dev/null
    echo "Granted $role to $WORKER_SA_EMAIL"
done

# Grant Default Compute SA (used by Agents) permissions:
# - Cloud Tasks Enqueuer (to schedule tasks)
# - Service Account User (to act as itself)
# - Run Invoker (to call Task Worker)

# Agent Identity SA
AGENT_SA_NAME="shoopet-agent-identity"
AGENT_SA_EMAIL="$AGENT_SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"

if ! gcloud iam service-accounts describe "$AGENT_SA_EMAIL" --project "$PROJECT_ID" >/dev/null 2>&1; then
    gcloud iam service-accounts create "$AGENT_SA_NAME" \
        --display-name "Shoopet Agent Identity" \
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

# Grant Agent Identity SA permission to create tokens for Task Worker SA
echo "Granting token creator permission on Task Worker SA..."
gcloud iam service-accounts add-iam-policy-binding "$WORKER_SA_EMAIL" \
    --member="serviceAccount:$AGENT_SA_EMAIL" \
    --role="roles/iam.serviceAccountTokenCreator" \
    --project="$PROJECT_ID" \
    --quiet >/dev/null
echo "Granted iam.serviceAccountTokenCreator on $WORKER_SA_EMAIL to $AGENT_SA_EMAIL"

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
echo "Task Worker SA: $WORKER_SA_EMAIL"
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
echo "  - Create OIDC tokens for task-worker SA"
echo "=========================================="
