# Agent Identity Principal IAM bindings
#
# These bindings use the Vertex AI Reasoning Engine's built-in identity
# (principal:// format) sourced directly from the managed resources in agents.tf.

locals {
  project_number = data.google_project.project.number

  # principal:// prefix + effectiveIdentity returned by the provider
  personal_agent_principal = "principal://${google_vertex_ai_reasoning_engine.personal_agent.spec[0].effective_identity}"

  agent_engine_project_roles = [
    "roles/cloudtasks.enqueuer",
    "roles/cloudtasks.viewer",
    "roles/run.invoker",
    "roles/datastore.user",
    "roles/serviceusage.serviceUsageConsumer",
    "roles/aiplatform.user",
    "roles/secretmanager.secretAccessor",
    "roles/bigquery.dataEditor",
    "roles/bigquery.jobUser",
    "roles/cloudtrace.agent",
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
  ]

  # Map of role → {principal, role} for the personal agent
  all_engine_bindings = {
    for role in local.agent_engine_project_roles :
    "personal_${role}" => { principal = local.personal_agent_principal, role = role }
  }
}

# Project-level role bindings for agent identity principal
resource "google_project_iam_member" "agent_engine_principals" {
  for_each = local.all_engine_bindings

  project = var.project_id
  role    = each.value.role
  member  = each.value.principal
}

# ── SA-level bindings on SMS gateway SA ───────────────────────────────────────

resource "google_service_account_iam_member" "personal_agent_token_creator_on_sms_gateway" {
  service_account_id = google_service_account.sms_gateway.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = local.personal_agent_principal
}

resource "google_service_account_iam_member" "personal_agent_sa_user_on_sms_gateway" {
  service_account_id = google_service_account.sms_gateway.name
  role               = "roles/iam.serviceAccountUser"
  member             = local.personal_agent_principal
}

# ── IAM Connector for 3-legged OAuth (Agent Identity) ─────────────────────────
# NOTE: google_iam_connector is not yet supported by the google-beta Terraform
# provider (API is Pre-GA). Provision the connector manually via gcloud or the
# Cloud Console, then set var.iam_connector_google_personal_name to the full
# resource name. Uncomment these resources once the provider gains support.
#
# resource "google_iam_connector" "google_personal" {
#   provider     = google-beta
#   project      = var.project_id
#   location     = "global"
#   connector_id = "google-personal-${terraform.workspace == "default" ? "prod" : terraform.workspace}"
#
#   oauth_config {
#     client_id     = var.google_oauth_client_id
#     client_secret = var.google_oauth_client_secret
#     scopes = [
#       "https://www.googleapis.com/auth/calendar.events",
#       "https://www.googleapis.com/auth/drive",
#       "https://www.googleapis.com/auth/documents",
#       "https://www.googleapis.com/auth/spreadsheets",
#       "https://www.googleapis.com/auth/gmail.readonly",
#       "https://www.googleapis.com/auth/gmail.modify",
#       "https://www.googleapis.com/auth/userinfo.email",
#       "openid",
#     ]
#     redirect_uris = ["${var.oauth_base_url}/oauth/connector/callback"]
#   }
#
#   depends_on = [google_project_service.apis]
# }
#
# resource "google_iam_connector_iam_member" "personal_agent_connector_user" {
#   provider  = google-beta
#   project   = var.project_id
#   location  = "global"
#   connector = google_iam_connector.google_personal.connector_id
#   role      = "roles/iamconnectors.user"
#   member    = local.personal_agent_principal
# }
