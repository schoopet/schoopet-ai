# ── task-worker SA ────────────────────────────────────────────────────────────
# Uses custom agentEngineUser role instead of roles/aiplatform.user — task
# worker only needs to query/get reasoning engines, not full Vertex AI access.

locals {
  task_worker_roles = [
    "roles/datastore.user",
    "roles/iam.serviceAccountTokenCreator",
    "roles/run.invoker",
  ]

  sms_gateway_roles = [
    "roles/secretmanager.secretAccessor",
    "roles/datastore.user", # Firestore: session storage + OAuth token cache
  ]
}

resource "google_project_iam_member" "task_worker" {
  for_each = toset(local.task_worker_roles)

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.task_worker.email}"
}

resource "google_project_iam_member" "task_worker_agent_engine_user" {
  project = var.project_id
  role    = google_project_iam_custom_role.agent_engine_user.id
  member  = "serviceAccount:${google_service_account.task_worker.email}"
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

# ── SMS Gateway Cloud Run invoke ───────────────────────────────────────────────
# Twilio, Slack, Telegram, and Discord all call the gateway without GCP bearer
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

