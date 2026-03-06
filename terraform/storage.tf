resource "google_storage_bucket" "artifacts" {
  project                     = var.project_id
  name                        = var.artifact_bucket_name
  location                    = "US-CENTRAL1"
  uniform_bucket_level_access = true

  depends_on = [google_project_service.apis["run.googleapis.com"]]
}
