# Agent Identity Principal IAM bindings
#
# These bindings use the Vertex AI Reasoning Engine's built-in identity
# (principal:// format) rather than a service account. They are created only
# when the corresponding engine ID variable is non-empty.
#
# Run after deploying agents:
#   terraform apply -var-file=environments/wib-boss-finder.tfvars \
#     -var="team_agent_engine_id=2114234416376053760"

locals {
  project_number = data.google_project.project.number

  team_agent_principal = var.team_agent_engine_id != "" ? "principal://agents.global.proj-${local.project_number}.system.id.goog/resources/aiplatform/projects/${local.project_number}/locations/${var.region}/reasoningEngines/${var.team_agent_engine_id}" : ""

  personal_agent_principal = var.personal_agent_engine_id != "" ? "principal://agents.global.proj-${local.project_number}.system.id.goog/resources/aiplatform/projects/${local.project_number}/locations/${var.region}/reasoningEngines/${var.personal_agent_engine_id}" : ""

  agent_engine_project_roles = [
    "roles/cloudtasks.enqueuer",
    "roles/run.invoker",
    "roles/datastore.user",
    "roles/serviceusage.serviceUsageConsumer",
    "roles/aiplatform.user",
    "roles/secretmanager.secretAccessor",
    "roles/bigquery.dataEditor",
    "roles/bigquery.jobUser",
  ]

  # Build flat map of {agent_type}_{role} → {principal, role} for all active agents
  team_engine_bindings = var.team_agent_engine_id != "" ? {
    for role in local.agent_engine_project_roles :
    "team_${role}" => { principal = local.team_agent_principal, role = role }
  } : {}

  personal_engine_bindings = var.personal_agent_engine_id != "" ? {
    for role in local.agent_engine_project_roles :
    "personal_${role}" => { principal = local.personal_agent_principal, role = role }
  } : {}

  all_engine_bindings = merge(local.team_engine_bindings, local.personal_engine_bindings)
}

# Project-level role bindings for agent identity principals
resource "google_project_iam_member" "agent_engine_principals" {
  for_each = local.all_engine_bindings

  project = var.project_id
  role    = each.value.role
  member  = each.value.principal
}

# ── SA-level bindings on task-worker SA ───────────────────────────────────────

resource "google_service_account_iam_member" "team_agent_token_creator_on_task_worker" {
  count = var.team_agent_engine_id != "" ? 1 : 0

  service_account_id = google_service_account.task_worker.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = local.team_agent_principal
}

resource "google_service_account_iam_member" "team_agent_sa_user_on_task_worker" {
  count = var.team_agent_engine_id != "" ? 1 : 0

  service_account_id = google_service_account.task_worker.name
  role               = "roles/iam.serviceAccountUser"
  member             = local.team_agent_principal
}

resource "google_service_account_iam_member" "personal_agent_token_creator_on_task_worker" {
  count = var.personal_agent_engine_id != "" ? 1 : 0

  service_account_id = google_service_account.task_worker.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = local.personal_agent_principal
}

resource "google_service_account_iam_member" "personal_agent_sa_user_on_task_worker" {
  count = var.personal_agent_engine_id != "" ? 1 : 0

  service_account_id = google_service_account.task_worker.name
  role               = "roles/iam.serviceAccountUser"
  member             = local.personal_agent_principal
}
