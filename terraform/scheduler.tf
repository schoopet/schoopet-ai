# Cloud Scheduler — Scheduled task requeue
#
# Scheduled tasks beyond Cloud Tasks' 720-hour window are stored in Firestore
# without a cloud_task_name. This job runs weekly and creates Cloud Tasks for
# any scheduled tasks that are now within the window.

resource "google_service_account" "task_requeue_scheduler" {
  project      = var.project_id
  account_id   = "task-requeue-scheduler"
  display_name = "Task Requeue Scheduler"
}

resource "google_cloud_run_v2_service_iam_member" "task_requeue_scheduler_invoke" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.task_worker.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.task_requeue_scheduler.email}"
}

resource "google_cloud_scheduler_job" "task_requeue" {
  project   = var.project_id
  region    = var.region
  name      = "task-requeue-scheduled"
  schedule  = "0 9 * * 1"  # Every Monday at 09:00 UTC
  time_zone = "UTC"

  http_target {
    http_method = "POST"
    uri         = "${google_cloud_run_v2_service.task_worker.uri}/requeue-scheduled-tasks"

    oidc_token {
      service_account_email = google_service_account.task_requeue_scheduler.email
      audience              = google_cloud_run_v2_service.task_worker.uri
    }
  }

  depends_on = [
    google_project_service.apis["cloudscheduler.googleapis.com"],
    google_cloud_run_v2_service_iam_member.task_requeue_scheduler_invoke,
  ]
}

# Cloud Scheduler — Gmail watch renewal
#
# Gmail watch registrations expire every ~7 days. This job hits the internal
# /internal/email/renew-watch endpoint every 6 days to renew both the system
# account watch and all personal user watches.

resource "google_service_account" "gmail_watch_scheduler" {
  project      = var.project_id
  account_id   = "gmail-watch-scheduler"
  display_name = "Gmail Watch Renewal Scheduler"
}

# Allow the scheduler SA to invoke the SMS gateway Cloud Run service.
resource "google_cloud_run_v2_service_iam_member" "scheduler_invoke" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.sms_gateway.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.gmail_watch_scheduler.email}"
}

resource "google_cloud_scheduler_job" "gmail_watch_renewal" {
  project  = var.project_id
  region   = var.region
  name     = "gmail-watch-renewal"
  schedule = "0 9 */6 * *"  # Every 6 days at 09:00 UTC
  time_zone = "UTC"

  http_target {
    http_method = "GET"
    uri = "${google_cloud_run_v2_service.sms_gateway.uri}/internal/email/renew-watch?topic=${urlencode("projects/${var.project_id}/topics/${google_pubsub_topic.email_notifications.name}")}"

    oidc_token {
      service_account_email = google_service_account.gmail_watch_scheduler.email
      audience              = google_cloud_run_v2_service.sms_gateway.uri
    }
  }

  depends_on = [
    google_project_service.apis["cloudscheduler.googleapis.com"],
    google_cloud_run_v2_service_iam_member.scheduler_invoke,
  ]
}
