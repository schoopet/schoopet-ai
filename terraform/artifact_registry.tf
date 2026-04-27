resource "google_artifact_registry_repository" "docker" {
  project       = var.project_id
  location      = "us"
  repository_id = "schoopet"
  format        = "DOCKER"
  description   = "Docker images for Schoopet services"

  depends_on = [google_project_service.apis["artifactregistry.googleapis.com"]]
}
