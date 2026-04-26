output "sms_gateway_url" {
  description = "Deployed URL of the SMS Gateway Cloud Run service"
  value       = google_cloud_run_v2_service.sms_gateway.uri
}

output "task_worker_url" {
  description = "Deployed URL of the Task Worker Cloud Run service"
  value       = google_cloud_run_v2_service.task_worker.uri
}

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

output "personal_agent_resource_name" {
  description = "Full resource name of the personal agent reasoning engine"
  value       = google_vertex_ai_reasoning_engine.personal_agent.name
}

output "personal_agent_effective_identity" {
  description = "effectiveIdentity of the personal agent (use with principal:// prefix for IAM)"
  value       = google_vertex_ai_reasoning_engine.personal_agent.spec[0].effective_identity
}
