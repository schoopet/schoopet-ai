resource "google_bigquery_dataset" "agent_token_usage" {
  project    = var.project_id
  dataset_id = "agent_token_usage"
  location   = "US"

  description = "Agent token usage logs exported from Cloud Logging"

  depends_on = [google_project_service.apis["bigquery.googleapis.com"]]
}

resource "google_logging_project_sink" "agent_token_usage" {
  project     = var.project_id
  name        = "agent-token-usage-to-bq"
  destination = "bigquery.googleapis.com/projects/${var.project_id}/datasets/${google_bigquery_dataset.agent_token_usage.dataset_id}"

  filter = "logName=\"projects/${var.project_id}/logs/agent_token_usage\""

  bigquery_options {
    use_partitioned_tables = true
  }

  depends_on = [google_bigquery_dataset.agent_token_usage]
}

resource "google_bigquery_dataset_iam_member" "agent_token_usage_sink_writer" {
  project    = var.project_id
  dataset_id = google_bigquery_dataset.agent_token_usage.dataset_id
  role       = "roles/bigquery.dataEditor"
  member     = google_logging_project_sink.agent_token_usage.writer_identity
}
