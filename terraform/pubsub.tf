resource "google_pubsub_topic" "email_notifications" {
  project = var.project_id
  name    = "email-notifications"

  depends_on = [google_project_service.apis["pubsub.googleapis.com"]]
}

# ── Gmail push subscription ───────────────────────────────────────────────────
# Pub/Sub delivers Gmail notifications to the SMS gateway's /webhook/email
# endpoint. A dedicated SA is used so the handler can verify the OIDC token
# and reject requests not from this subscription.

resource "google_service_account" "email_push" {
  project      = var.project_id
  account_id   = "email-push"
  display_name = "Gmail Pub/Sub Push"
}

# Allow the push SA to invoke the SMS gateway Cloud Run service.
resource "google_cloud_run_v2_service_iam_member" "email_push_invoke" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.sms_gateway.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.email_push.email}"
}

# Allow Pub/Sub to generate OIDC tokens for this SA when pushing messages.
resource "google_project_iam_member" "email_push_token_creator" {
  project = var.project_id
  role    = "roles/iam.serviceAccountTokenCreator"
  member  = "serviceAccount:service-${data.google_project.project.number}@gcp-sa-pubsub.iam.gserviceaccount.com"
}

resource "google_pubsub_subscription" "email_notifications_push" {
  project = var.project_id
  name    = "email-notifications-push"
  topic   = google_pubsub_topic.email_notifications.name

  ack_deadline_seconds       = 60
  message_retention_duration = "600s" # 10 minutes — notifications are time-sensitive

  push_config {
    push_endpoint = "${google_cloud_run_v2_service.sms_gateway.uri}/webhook/email"

    oidc_token {
      service_account_email = google_service_account.email_push.email
      # Audience matches SMS_GATEWAY_URL — verified by the handler.
      audience = google_cloud_run_v2_service.sms_gateway.uri
    }
  }

  retry_policy {
    minimum_backoff = "10s"
    maximum_backoff = "300s"
  }

  depends_on = [
    google_project_service.apis["pubsub.googleapis.com"],
    google_cloud_run_v2_service_iam_member.email_push_invoke,
  ]
}
