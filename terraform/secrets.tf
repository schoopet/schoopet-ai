# Secret resources only — no secret values are stored here.
# Values must be added manually via:
#   echo -n "VALUE" | gcloud secrets versions add SECRET_NAME --data-file=-
# or via the GCP Console.

locals {
  secret_names = [
    "google-oauth-client-id",
    "google-oauth-client-secret",
    "oauth-hmac-secret",
    "discord-bot-token",
  ]
}

resource "google_secret_manager_secret" "secrets" {
  for_each = toset(local.secret_names)

  project   = var.project_id
  secret_id = each.value

  replication {
    auto {}
  }

  depends_on = [google_project_service.apis["secretmanager.googleapis.com"]]
}
