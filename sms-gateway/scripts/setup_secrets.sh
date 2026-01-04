#!/bin/bash
# Set up Secret Manager secrets for Twilio credentials
#
# Usage:
#   ./scripts/setup_secrets.sh
#
# This script will:
#   1. Create secrets in Secret Manager (if they don't exist)
#   2. Prompt you to enter secret values
#   3. Grant Cloud Run access to read the secrets

set -e

# Configuration
PROJECT_ID="${GOOGLE_CLOUD_PROJECT:-mmontan-ml}"
REGION="${GOOGLE_CLOUD_LOCATION:-us-central1}"

echo "=========================================="
echo "Setting up Secret Manager for SMS Gateway"
echo "=========================================="
echo "Project: $PROJECT_ID"
echo "=========================================="

# Get the project number (needed for service account)
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

echo ""
echo "Compute service account: $COMPUTE_SA"

# Function to create secret if it doesn't exist
create_secret_if_not_exists() {
    local secret_name=$1

    if gcloud secrets describe "$secret_name" --project="$PROJECT_ID" &>/dev/null; then
        echo "Secret '$secret_name' already exists"
    else
        echo "Creating secret '$secret_name'..."
        gcloud secrets create "$secret_name" \
            --project="$PROJECT_ID" \
            --replication-policy="automatic"
    fi
}

# Function to add a secret version
add_secret_version() {
    local secret_name=$1
    local prompt_text=$2

    echo ""
    echo -n "$prompt_text: "
    read -s secret_value
    echo ""

    if [ -z "$secret_value" ]; then
        echo "Skipping $secret_name (empty value)"
        return
    fi

    echo "$secret_value" | gcloud secrets versions add "$secret_name" \
        --project="$PROJECT_ID" \
        --data-file=-

    echo "Added version to $secret_name"
}

# Function to grant access to Cloud Run
grant_access() {
    local secret_name=$1

    echo "Granting access to $secret_name for Cloud Run..."
    gcloud secrets add-iam-policy-binding "$secret_name" \
        --project="$PROJECT_ID" \
        --member="serviceAccount:$COMPUTE_SA" \
        --role="roles/secretmanager.secretAccessor" \
        --quiet
}

# Create secrets
echo ""
echo "Creating secrets..."
create_secret_if_not_exists "twilio-account-sid"
create_secret_if_not_exists "twilio-auth-token"
create_secret_if_not_exists "twilio-phone-number"

# Ask if user wants to set values
echo ""
echo "Do you want to add/update secret values? (y/n)"
read -r response

if [[ "$response" =~ ^[Yy]$ ]]; then
    echo ""
    echo "Enter your Twilio credentials:"
    echo "(Get these from https://console.twilio.com)"
    echo ""

    add_secret_version "twilio-account-sid" "Enter TWILIO_ACCOUNT_SID (starts with AC)"
    add_secret_version "twilio-auth-token" "Enter TWILIO_AUTH_TOKEN"
    add_secret_version "twilio-phone-number" "Enter TWILIO_PHONE_NUMBER (E.164 format, e.g., +14155551234)"
fi

# Grant access
echo ""
echo "Granting Cloud Run access to secrets..."
grant_access "twilio-account-sid"
grant_access "twilio-auth-token"
grant_access "twilio-phone-number"

echo ""
echo "=========================================="
echo "Secret Manager Setup Complete!"
echo "=========================================="
echo ""
echo "Secrets created:"
echo "  - twilio-account-sid"
echo "  - twilio-auth-token"
echo "  - twilio-phone-number"
echo ""
echo "Next steps:"
echo "  1. Run ./scripts/setup_firestore.py to create Firestore collection"
echo "  2. Run ./scripts/deploy.sh to deploy to Cloud Run"
echo "=========================================="
