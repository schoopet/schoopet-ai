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

variable "sms_gateway_image" {
  description = "Container image for the SMS Gateway Cloud Run service. Defaults to us-docker.pkg.dev/{project_id}/schoopet/shoopet-sms-gateway:latest"
  type        = string
  default     = null
}

variable "oauth_base_url" {
  description = "Base URL for OAuth callbacks (e.g. https://api.schoopet.com). Used to set GOOGLE_OAUTH_REDIRECT_URI on the SMS Gateway."
  type        = string
  default     = ""
}

variable "sms_gateway_url" {
  description = "Public URL of the SMS Gateway Cloud Run service. Used as the Pub/Sub push endpoint audience for OIDC verification."
  type        = string
  default     = ""
}

variable "email_drive_folder_id" {
  description = "Google Drive folder ID for email attachment storage"
  type        = string
  default     = ""
}

variable "email_sheet_id" {
  description = "Google Sheets ID for email logging"
  type        = string
  default     = ""
}

variable "discord_application_id" {
  description = "Discord application (client) ID"
  type        = string
  default     = ""
}

variable "discord_public_key" {
  description = "Discord application Ed25519 public key (from Developer Portal)"
  type        = string
  default     = ""
}

variable "google_oauth_client_id" {
  description = "Google OAuth 2.0 client ID for IAM connector"
  type        = string
  default     = ""
}

variable "google_oauth_client_secret" {
  description = "Google OAuth 2.0 client secret for IAM connector"
  type        = string
  sensitive   = true
  default     = ""
}

variable "iam_connector_google_personal_name" {
  description = "Full IAM connector resource name (projects/{proj}/locations/global/connectors/{id}). Set manually until google_iam_connector is GA in the Terraform provider."
  type        = string
  default     = ""
}
