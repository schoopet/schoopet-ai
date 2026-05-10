locals {
  sms_gateway_roles = [
    # OAuth refresh tokens are created per user at runtime, then updated/read/deleted.
    # The gateway needs secret create/version/write/delete permissions, not just read.
    "roles/secretmanager.admin",
    "roles/datastore.user", # Firestore: session storage + OAuth token cache
    "roles/cloudtasks.enqueuer",
    "roles/cloudtasks.viewer",
    "roles/iam.serviceAccountTokenCreator",
  ]
}

# ── schoopet-sms-gateway SA ───────────────────────────────────────────────────

resource "google_project_iam_member" "sms_gateway" {
  for_each = toset(local.sms_gateway_roles)

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.sms_gateway.email}"
}

# SMS gateway queries agent reasoning engines to route inbound messages.
resource "google_project_iam_member" "sms_gateway_agent_engine_user" {
  project = var.project_id
  role    = google_project_iam_custom_role.agent_engine_user.id
  member  = "serviceAccount:${google_service_account.sms_gateway.email}"
}

# Gateway requeue creates Cloud Tasks that use the gateway service account as
# their OIDC identity, so the gateway SA needs actAs on itself.
resource "google_service_account_iam_member" "sms_gateway_sa_user_on_self" {
  service_account_id = google_service_account.sms_gateway.name
  role               = "roles/iam.serviceAccountUser"
  member             = "serviceAccount:${google_service_account.sms_gateway.email}"
}

# ── SMS Gateway Cloud Run invoke ───────────────────────────────────────────────
# Discord and other public channel webhooks call the gateway without GCP bearer
# tokens, so the service must be publicly invocable. The email push endpoint
# additionally verifies its own OIDC token (email-push SA) in the handler.

resource "google_cloud_run_v2_service_iam_member" "sms_gateway_public_invoke" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.sms_gateway.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# ── Project editors ───────────────────────────────────────────────────────────

resource "google_project_iam_member" "editors" {
  for_each = toset(var.project_editors)

  project = var.project_id
  role    = "roles/editor"
  member  = each.value
}
