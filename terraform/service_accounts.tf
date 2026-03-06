resource "google_service_account" "task_worker" {
  project      = var.project_id
  account_id   = "task-worker"
  display_name = "Schoopet Task Worker"
}

resource "google_service_account" "sms_gateway" {
  project      = var.project_id
  account_id   = "schoopet-sms-gateway"
  display_name = "Schoopet SMS Gateway"
}

