# CI/CD Setup

One-time steps to enable automatic deployments on push to `main`.

## 1. Create a deploy service account

If you don't already have one:

```bash
gcloud iam service-accounts create github-deploy \
  --project=schoopet-prod \
  --display-name="GitHub Actions Deploy"
```

Grant it the permissions needed to build, push, and deploy:

```bash
PROJECT=schoopet-prod
SA=github-deploy@schoopet-prod.iam.gserviceaccount.com

for role in \
  roles/cloudbuild.builds.editor \
  roles/artifactregistry.writer \
  roles/run.admin \
  roles/storage.admin \
  roles/secretmanager.secretAccessor \
  roles/iam.serviceAccountUser; do
  gcloud projects add-iam-policy-binding $PROJECT \
    --member="serviceAccount:$SA" \
    --role="$role"
done
```

For the agent deployment, also grant:

```bash
gcloud projects add-iam-policy-binding schoopet-prod \
  --member="serviceAccount:$SA" \
  --role="roles/aiplatform.user"
```

## 2. Set up Workload Identity Federation

```bash
PROJECT=schoopet-prod
SA=github-deploy@schoopet-prod.iam.gserviceaccount.com
REPO=schoopet/schoopet-ai

# Create the WIF pool
gcloud iam workload-identity-pools create github \
  --project=$PROJECT \
  --location=global \
  --display-name="GitHub Actions"

# Create the OIDC provider
gcloud iam workload-identity-pools providers create-oidc github-actions \
  --project=$PROJECT \
  --location=global \
  --workload-identity-pool=github \
  --issuer-uri="https://token.actions.githubusercontent.com" \
  --attribute-mapping="google.subject=assertion.sub,attribute.repository=assertion.repository" \
  --attribute-condition="assertion.repository=='$REPO'"

# Allow the SA to be impersonated from this repo
POOL=$(gcloud iam workload-identity-pools describe github \
  --project=$PROJECT \
  --location=global \
  --format="value(name)")

gcloud iam service-accounts add-iam-policy-binding $SA \
  --project=$PROJECT \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/${POOL}/attribute.repository/${REPO}"
```

Get the provider resource name for the GitHub secret:

```bash
gcloud iam workload-identity-pools providers describe github-actions \
  --project=$PROJECT \
  --location=global \
  --workload-identity-pool=github \
  --format="value(name)"
```

## 3. Add GitHub secrets

In the repo: **Settings → Secrets and variables → Actions → New repository secret**

| Secret | Value |
|--------|-------|
| `GCP_WORKLOAD_IDENTITY_PROVIDER` | output of the `describe` command above |
| `GCP_SERVICE_ACCOUNT` | `github-deploy@schoopet-prod.iam.gserviceaccount.com` |

## 4. Verify OAuth secrets exist in Secret Manager

The deploy script fetches `google-oauth-client-id` and `google-oauth-client-secret`
from Secret Manager. Confirm their values are populated:

```bash
gcloud secrets versions access latest \
  --secret=google-oauth-client-id --project=schoopet-prod

gcloud secrets versions access latest \
  --secret=google-oauth-client-secret --project=schoopet-prod
```

If either is missing, add it:

```bash
echo -n "YOUR_VALUE" | gcloud secrets versions add google-oauth-client-id \
  --data-file=- --project=schoopet-prod
```
