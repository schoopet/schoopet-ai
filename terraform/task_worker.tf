locals {
  task_worker_image = coalesce(var.task_worker_image, "us-docker.pkg.dev/${var.project_id}/schoopet/schoopet-task-worker:latest")
}

resource "google_cloud_run_v2_service" "task_worker" {
  project  = var.project_id
  name     = "schoopet-task-worker"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  deletion_protection = false

  template {
    service_account = google_service_account.task_worker.email
    timeout         = "900s"

    scaling {
      min_instance_count = 0
      max_instance_count = 10
    }

    containers {
      image = local.task_worker_image

      resources {
        limits = {
          memory = "1Gi"
          cpu    = "1000m"
        }
      }

      env {
        name  = "GOOGLE_CLOUD_PROJECT"
        value = var.project_id
      }
      env {
        name  = "GOOGLE_CLOUD_LOCATION"
        value = var.region
      }
      env {
        name  = "PERSONAL_AGENT_ENGINE_ID"
        value = local.personal_agent_engine_id
      }
      env {
        name  = "SMS_GATEWAY_URL"
        value = google_cloud_run_v2_service.sms_gateway.uri
      }
      env {
        name  = "TASK_WORKER_URL"
        value = var.task_worker_url
      }
      env {
        name  = "TASK_WORKER_SA"
        value = google_service_account.task_worker.email
      }
      env {
        name  = "TASK_REQUEUE_SCHEDULER_SA"
        value = google_service_account.task_requeue_scheduler.email
      }
    }
  }

  depends_on = [google_project_service.apis["run.googleapis.com"]]
}
