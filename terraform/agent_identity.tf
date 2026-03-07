# Agent Identity Principal IAM bindings
#
# These bindings use the Vertex AI Reasoning Engine's built-in identity
# (principal:// format) sourced directly from the managed resources in agents.tf.

locals {
  project_number = data.google_project.project.number

  # principal:// prefix + effectiveIdentity returned by the provider
  team_agent_principal     = "principal://${google_vertex_ai_reasoning_engine.team_agent.spec[0].effective_identity}"
  personal_agent_principal = "principal://${google_vertex_ai_reasoning_engine.personal_agent.spec[0].effective_identity}"

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

  # Flat map of {agent_type}_{role} → {principal, role} for all agents
  all_engine_bindings = merge(
    { for role in local.agent_engine_project_roles :
    "team_${role}" => { principal = local.team_agent_principal, role = role } },
    { for role in local.agent_engine_project_roles :
    "personal_${role}" => { principal = local.personal_agent_principal, role = role } },
  )
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
  service_account_id = google_service_account.task_worker.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = local.team_agent_principal
}

resource "google_service_account_iam_member" "team_agent_sa_user_on_task_worker" {
  service_account_id = google_service_account.task_worker.name
  role               = "roles/iam.serviceAccountUser"
  member             = local.team_agent_principal
}

resource "google_service_account_iam_member" "personal_agent_token_creator_on_task_worker" {
  service_account_id = google_service_account.task_worker.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = local.personal_agent_principal
}

resource "google_service_account_iam_member" "personal_agent_sa_user_on_task_worker" {
  service_account_id = google_service_account.task_worker.name
  role               = "roles/iam.serviceAccountUser"
  member             = local.personal_agent_principal
}
