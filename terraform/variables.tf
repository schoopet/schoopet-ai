variable "project_id" {
  description = "GCP project ID"
  type        = string
}

variable "region" {
  description = "GCP region"
  type        = string
  default     = "us-central1"
}

variable "artifact_bucket_name" {
  description = "GCS bucket name for agent artifacts"
  type        = string
}

variable "project_editors" {
  description = "List of users/groups to grant roles/editor on the project (e.g. [\"user:alice@example.com\"])"
  type        = list(string)
  default     = []
}
