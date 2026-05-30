locals {
  required_apis = [
    "cloudtasks.googleapis.com",
    "run.googleapis.com",
    "firestore.googleapis.com",
    "aiplatform.googleapis.com",
    "cloudbuild.googleapis.com",
    "artifactregistry.googleapis.com",
    "secretmanager.googleapis.com",
    "pubsub.googleapis.com",
    "bigquery.googleapis.com",
    "iam.googleapis.com",
    "cloudscheduler.googleapis.com",
    "cloudtrace.googleapis.com",
    "logging.googleapis.com",
    "monitoring.googleapis.com",
    "telemetry.googleapis.com",
    "calendar-json.googleapis.com",
    "gmail.googleapis.com",
    "docs.googleapis.com",
    "sheets.googleapis.com",
    "drive.googleapis.com",
    "iamconnectors.googleapis.com",
    "iamconnectorcredentials.googleapis.com",
    "recommender.googleapis.com",
    "billingbudgets.googleapis.com",
    "cloudresourcemanager.googleapis.com",
  ]
}

resource "google_project_service" "apis" {
  for_each = toset(local.required_apis)

  project            = var.project_id
  service            = each.value
  disable_on_destroy = false
}
