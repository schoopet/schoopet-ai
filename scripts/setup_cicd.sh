#!/bin/bash
# Set up GitHub Actions CI/CD for automatic deployments.
#
# Creates the deploy service account, grants required IAM roles, configures
# Workload Identity Federation, and verifies OAuth secrets in Secret Manager.
# Re-running is idempotent — existing resources are left unchanged.
#
# Usage:
#   ./scripts/setup_cicd.sh              # prod (default)
#   ./scripts/setup_cicd.sh --env=dev

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

# Parse --env flag
ENV_NAME="prod"
for arg in "$@"; do
    case $arg in
        --env=*) ENV_NAME="${arg#*=}" ;;
    esac
done

# Load environment
ENV_FILE="$REPO_ROOT/environments/${ENV_NAME}.env"
if [ ! -f "$ENV_FILE" ]; then
    echo -e "${RED}Error: environments/${ENV_NAME}.env not found${NC}"
    exit 1
fi
set -a; source "$ENV_FILE"; set +a

PROJECT="${GOOGLE_CLOUD_PROJECT}"
REPO="schoopet/schoopet-ai"
SA_NAME="github-deploy"
SA_EMAIL="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"
POOL_NAME="github"
PROVIDER_NAME="github-actions"

echo -e "${BLUE}======================================${NC}"
echo -e "${BLUE}  CI/CD Setup — ${ENV_NAME}${NC}"
echo -e "${BLUE}======================================${NC}"
echo -e "Project:  ${GREEN}$PROJECT${NC}"
echo -e "Repo:     ${GREEN}$REPO${NC}"
echo -e "SA:       ${GREEN}$SA_EMAIL${NC}"
echo ""

# ── 1. Deploy service account ─────────────────────────────────────────────────
echo -e "${BLUE}[1/4] Deploy service account${NC}"
if gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT" &>/dev/null; then
    echo -e "  ${YELLOW}Already exists — skipping creation${NC}"
else
    gcloud iam service-accounts create "$SA_NAME" \
        --project="$PROJECT" \
        --display-name="GitHub Actions Deploy"
    echo -e "  ${GREEN}Created $SA_EMAIL${NC}"
fi

# ── 2. IAM roles ──────────────────────────────────────────────────────────────
echo ""
echo -e "${BLUE}[2/4] Granting IAM roles${NC}"
ROLES=(
    roles/cloudbuild.builds.editor
    roles/artifactregistry.writer
    roles/run.admin
    roles/storage.admin
    roles/secretmanager.secretAccessor
    roles/iam.serviceAccountUser
    roles/aiplatform.user
)
for role in "${ROLES[@]}"; do
    gcloud projects add-iam-policy-binding "$PROJECT" \
        --member="serviceAccount:$SA_EMAIL" \
        --role="$role" \
        --condition=None \
        --quiet
    echo -e "  ${GREEN}✓${NC} $role"
done

# ── 3. Workload Identity Federation ───────────────────────────────────────────
echo ""
echo -e "${BLUE}[3/4] Workload Identity Federation${NC}"

# Pool
if gcloud iam workload-identity-pools describe "$POOL_NAME" \
        --project="$PROJECT" --location=global &>/dev/null; then
    echo -e "  ${YELLOW}Pool '$POOL_NAME' already exists — skipping${NC}"
else
    gcloud iam workload-identity-pools create "$POOL_NAME" \
        --project="$PROJECT" \
        --location=global \
        --display-name="GitHub Actions"
    echo -e "  ${GREEN}Created pool '$POOL_NAME'${NC}"
fi

POOL_RESOURCE=$(gcloud iam workload-identity-pools describe "$POOL_NAME" \
    --project="$PROJECT" --location=global --format="value(name)")

# Provider
if gcloud iam workload-identity-pools providers describe "$PROVIDER_NAME" \
        --project="$PROJECT" --location=global \
        --workload-identity-pool="$POOL_NAME" &>/dev/null; then
    echo -e "  ${YELLOW}Provider '$PROVIDER_NAME' already exists — skipping${NC}"
else
    gcloud iam workload-identity-pools providers create-oidc "$PROVIDER_NAME" \
        --project="$PROJECT" \
        --location=global \
        --workload-identity-pool="$POOL_NAME" \
        --issuer-uri="https://token.actions.githubusercontent.com" \
        --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
        --attribute-condition="assertion.repository=='$REPO'"
    echo -e "  ${GREEN}Created provider '$PROVIDER_NAME'${NC}"
fi

# SA binding
gcloud iam service-accounts add-iam-policy-binding "$SA_EMAIL" \
    --project="$PROJECT" \
    --role="roles/iam.workloadIdentityUser" \
    --member="principalSet://iam.googleapis.com/${POOL_RESOURCE}/attribute.repository/${REPO}" \
    --quiet
echo -e "  ${GREEN}SA bound to WIF pool for repo $REPO${NC}"

PROVIDER_RESOURCE=$(gcloud iam workload-identity-pools providers describe "$PROVIDER_NAME" \
    --project="$PROJECT" --location=global \
    --workload-identity-pool="$POOL_NAME" \
    --format="value(name)")

# ── 4. OAuth secrets in Secret Manager ────────────────────────────────────────
echo ""
echo -e "${BLUE}[4/4] OAuth secrets in Secret Manager${NC}"
for secret in google-oauth-client-id google-oauth-client-secret; do
    VALUE=$(gcloud secrets versions access latest \
        --secret="$secret" --project="$PROJECT" 2>/dev/null || true)
    if [ -n "$VALUE" ]; then
        echo -e "  ${GREEN}✓${NC} $secret is populated"
    else
        echo -e "  ${RED}✗${NC} $secret has no value — add it with:"
        echo -e "      echo -n \"YOUR_VALUE\" | gcloud secrets versions add $secret --data-file=- --project=$PROJECT"
    fi
done

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo -e "${GREEN}======================================${NC}"
echo -e "${GREEN}  Done — add these GitHub secrets:${NC}"
echo -e "${GREEN}======================================${NC}"
echo ""
echo -e "  ${BLUE}GCP_WORKLOAD_IDENTITY_PROVIDER${NC}"
echo -e "  $PROVIDER_RESOURCE"
echo ""
echo -e "  ${BLUE}GCP_SERVICE_ACCOUNT${NC}"
echo -e "  $SA_EMAIL"
echo ""
echo "Go to: https://github.com/${REPO}/settings/secrets/actions"
