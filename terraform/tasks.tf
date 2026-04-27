resource "google_cloud_tasks_queue" "async_agent_tasks" {
  project  = var.project_id
  name     = "async-agent-tasks"
  location = var.region

  rate_limits {
    max_concurrent_dispatches = 20
    max_dispatches_per_second = 5
  }

  retry_config {
    max_attempts  = 5
    min_backoff   = "10s"
    max_backoff   = "300s"
    max_doublings = 4
  }

  depends_on = [google_project_service.apis["cloudtasks.googleapis.com"]]
}
