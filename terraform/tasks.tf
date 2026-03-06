resource "google_cloud_tasks_queue" "async_agent_tasks" {
  project  = var.project_id
  name     = "async-agent-tasks"
  location = var.region

  depends_on = [google_project_service.apis["cloudtasks.googleapis.com"]]
}
