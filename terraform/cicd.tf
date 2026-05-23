# GitHub Actions CI/CD infrastructure
#
# Provisions the deploy SA, WIF pool/provider, and IAM bindings so that
# GitHub Actions can authenticate to GCP without long-lived service account keys.
#
# After applying, set these secrets in the GitHub repo settings:
#   GCP_WORKLOAD_IDENTITY_PROVIDER = terraform output -raw cicd_workload_identity_provider
#   GCP_SERVICE_ACCOUNT            = terraform output -raw cicd_service_account_email

locals {
  github_deploy_roles = [
    "roles/cloudbuild.builds.editor",
    "roles/artifactregistry.writer",
    "roles/run.admin",
    "roles/secretmanager.secretAccessor",
    "roles/iam.serviceAccountUser",
    "roles/aiplatform.user",
  ]
}

resource "google_service_account" "github_deploy" {
  project      = var.project_id
  account_id   = "github-deploy"
  display_name = "GitHub Actions Deploy"
}

resource "google_project_iam_member" "github_deploy" {
  for_each = toset(local.github_deploy_roles)

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.github_deploy.email}"
}

# Scoped to the Terraform state bucket only — not project-wide storage admin.
resource "google_storage_bucket_iam_member" "github_deploy_tf_state" {
  bucket = "schoopet-terraform-state"
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.github_deploy.email}"
}

resource "google_iam_workload_identity_pool" "github" {
  project                   = var.project_id
  workload_identity_pool_id = "github"
  display_name              = "GitHub Actions"
}

resource "google_iam_workload_identity_pool_provider" "github_actions" {
  project                            = var.project_id
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-actions"

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.repository" = "assertion.repository"
  }
  attribute_condition = "assertion.repository == '${var.github_repo}'"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }
}

resource "google_service_account_iam_member" "github_deploy_wif" {
  service_account_id = google_service_account.github_deploy.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repo}"
}
