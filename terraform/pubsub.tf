resource "google_pubsub_topic" "email_notifications" {
  project = var.project_id
  name    = "email-notifications"

  depends_on = [google_project_service.apis["pubsub.googleapis.com"]]
}
