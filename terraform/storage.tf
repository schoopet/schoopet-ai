resource "google_storage_bucket" "artifacts" {
  project                     = var.project_id
  name                        = var.artifact_bucket_name
  location                    = "US-CENTRAL1"
  uniform_bucket_level_access = true

  depends_on = [google_project_service.apis["run.googleapis.com"]]
}

# Agent Engine reads deployment packages and writes runtime artifacts (email attachments, etc.)
resource "google_storage_bucket_iam_member" "personal_agent_artifact_admin" {
  bucket = google_storage_bucket.artifacts.name
  role   = "roles/storage.objectAdmin"
  member = local.personal_agent_principal
}
