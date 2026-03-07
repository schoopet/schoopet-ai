# Import blocks for wib-boss-finder (mmontan-ml) pre-existing resources.
#
# Symlink this file into the terraform root before running for wib-boss-finder:
#   ln -sf wib-boss-finder/imports.tf imports-env.tf
#
# Usage (requires mirko.montanari@gmail.com ADC — org policy blocks mirko@ from writing mmontan-ml IAM):
#   ln -sf wib-boss-finder/imports.tf imports-env.tf
#   terraform init -reconfigure \
#     -backend-config="prefix=wib-boss-finder" \
#     -backend-config="bucket=mmontan-ml-terraform-state"
#   terraform plan -var-file=environments/wib-boss-finder.tfvars
#   terraform apply -var-file=environments/wib-boss-finder.tfvars -parallelism=1
#   rm imports-env.tf   # clean up after apply

import {
  id = "projects/${var.project_id}/serviceAccounts/task-worker@${var.project_id}.iam.gserviceaccount.com"
  to = google_service_account.task_worker
}

import {
  id = "projects/${var.project_id}/serviceAccounts/schoopet-sms-gateway@${var.project_id}.iam.gserviceaccount.com"
  to = google_service_account.sms_gateway
}

import {
  id = "projects/${var.project_id}/locations/${var.region}/queues/async-agent-tasks"
  to = google_cloud_tasks_queue.async_agent_tasks
}

import {
  id = var.artifact_bucket_name
  to = google_storage_bucket.artifacts
}

import {
  id = "projects/${var.project_id}/topics/email-notifications"
  to = google_pubsub_topic.email_notifications
}

import {
  id = "projects/${var.project_id}/secrets/twilio-account-sid"
  to = google_secret_manager_secret.secrets["twilio-account-sid"]
}

import {
  id = "projects/${var.project_id}/secrets/twilio-auth-token"
  to = google_secret_manager_secret.secrets["twilio-auth-token"]
}

import {
  id = "projects/${var.project_id}/secrets/twilio-phone-number"
  to = google_secret_manager_secret.secrets["twilio-phone-number"]
}

import {
  id = "projects/${var.project_id}/secrets/google-oauth-client-id"
  to = google_secret_manager_secret.secrets["google-oauth-client-id"]
}

import {
  id = "projects/${var.project_id}/secrets/google-oauth-client-secret"
  to = google_secret_manager_secret.secrets["google-oauth-client-secret"]
}

import {
  id = "projects/${var.project_id}/secrets/oauth-hmac-secret"
  to = google_secret_manager_secret.secrets["oauth-hmac-secret"]
}

import {
  id = "projects/${var.project_id}/secrets/twilio-whatsapp-number"
  to = google_secret_manager_secret.secrets["twilio-whatsapp-number"]
}

import {
  id = "projects/${var.project_id}/secrets/telegram-bot-token"
  to = google_secret_manager_secret.secrets["telegram-bot-token"]
}

import {
  id = "projects/${var.project_id}/secrets/slack-bot-token"
  to = google_secret_manager_secret.secrets["slack-bot-token"]
}

import {
  id = "projects/${var.project_id}/secrets/slack-signing-secret"
  to = google_secret_manager_secret.secrets["slack-signing-secret"]
}

import {
  id = "projects/${var.project_id}/roles/agentEngineUser"
  to = google_project_iam_custom_role.agent_engine_user
}

# Team agent engine — wib-boss-finder specific ID; other envs: omit or override
import {
  id = "projects/${var.project_id}/locations/${var.region}/reasoningEngines/2114234416376053760"
  to = google_vertex_ai_reasoning_engine.team_agent
}
