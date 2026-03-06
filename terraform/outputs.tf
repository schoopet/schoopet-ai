output "task_worker_sa_email" {
  description = "Task Worker service account email"
  value       = google_service_account.task_worker.email
}

output "sms_gateway_sa_email" {
  description = "SMS Gateway service account email"
  value       = google_service_account.sms_gateway.email
}


output "tasks_queue_path" {
  description = "Full Cloud Tasks queue path"
  value       = "projects/${var.project_id}/locations/${var.region}/queues/${google_cloud_tasks_queue.async_agent_tasks.name}"
}

output "artifact_bucket_name" {
  description = "GCS artifact bucket name"
  value       = google_storage_bucket.artifacts.name
}

output "email_pubsub_topic" {
  description = "Pub/Sub topic for email notifications"
  value       = "projects/${var.project_id}/topics/${google_pubsub_topic.email_notifications.name}"
}
