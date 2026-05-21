locals {
  sms_gateway_image        = coalesce(var.sms_gateway_image, "us-docker.pkg.dev/${var.project_id}/schoopet/shoopet-sms-gateway:latest")
  personal_agent_engine_id = regex("[^/]+$", google_vertex_ai_reasoning_engine.personal_agent.name)
}

resource "google_cloud_run_v2_service" "sms_gateway" {
  project  = var.project_id
  name     = "shoopet-sms-gateway"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  deletion_protection = false

  template {
    service_account = google_service_account.sms_gateway.email
    timeout         = "900s"

    scaling {
      min_instance_count = 1
      max_instance_count = 1
    }

    containers {
      image = local.sms_gateway_image

      resources {
        limits = {
          memory = "512Mi"
          cpu    = "1000m"
        }
      }

      # ── Plain env vars ────────────────────────────────────────────────────────
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
        name  = "EMAIL_PUBSUB_TOPIC"
        value = "projects/${var.project_id}/topics/${google_pubsub_topic.email_notifications.name}"
      }
      env {
        name  = "ARTIFACT_BUCKET_NAME"
        value = google_storage_bucket.artifacts.name
      }
      env {
        name  = "EMAIL_DRIVE_FOLDER_ID"
        value = var.email_drive_folder_id
      }
      env {
        name  = "EMAIL_SHEET_ID"
        value = var.email_sheet_id
      }
      env {
        name  = "DISCORD_APPLICATION_ID"
        value = var.discord_application_id
      }
      env {
        name  = "DISCORD_PUBLIC_KEY"
        value = var.discord_public_key
      }
      env {
        name  = "EMAIL_PUSH_SERVICE_ACCOUNT"
        value = google_service_account.email_push.email
      }
      env {
        name  = "IAM_CONNECTOR_GOOGLE_PERSONAL_NAME"
        value = var.iam_connector_google_personal_name
      }
      env {
        name  = "IAM_CONNECTOR_CONTINUE_URI"
        value = "${var.oauth_base_url}/oauth/connector/callback"
      }
      env {
        name  = "SMS_GATEWAY_URL"
        value = var.sms_gateway_url
      }

      # ── Secret env vars ───────────────────────────────────────────────────────
      env {
        name = "DISCORD_BOT_TOKEN"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.secrets["discord-bot-token"].secret_id
            version = "latest"
          }
        }
      }
    }
  }

  depends_on = [google_project_service.apis["run.googleapis.com"]]
}
