resource "google_project_iam_custom_role" "agent_engine_user" {
  project     = var.project_id
  role_id     = "agentEngineUser"
  title       = "Agent Engine User"
  description = "Minimal role for services that only need to query/get Vertex AI Reasoning Engines."
  permissions = [
    "aiplatform.reasoningEngines.query",
    "aiplatform.reasoningEngines.get",
  ]
}
