# Import blocks for existing mmontan-ml (wib-boss-finder) resources.
#
# These imports reconcile pre-existing GCP resources created by the bash setup
# scripts into Terraform state. Only relevant for the wib-boss-finder environment.
#
# For fresh environments (dev, prod): DELETE this file before running
# `terraform init` — the resources don't exist yet and will be created normally.
#
# Usage (requires mirko.montanari@gmail.com ADC — org policy blocks mirko@ from writing mmontan-ml IAM):
#   terraform init -reconfigure \
#     -backend-config="prefix=wib-boss-finder" \
#     -backend-config="bucket=mmontan-ml-terraform-state"
#   terraform plan -var-file=environments/wib-boss-finder.tfvars
#   terraform apply -var-file=environments/wib-boss-finder.tfvars -parallelism=1

import {
  id = "projects/mmontan-ml/serviceAccounts/task-worker@mmontan-ml.iam.gserviceaccount.com"
  to = google_service_account.task_worker
}


import {
  id = "projects/mmontan-ml/serviceAccounts/schoopet-sms-gateway@mmontan-ml.iam.gserviceaccount.com"
  to = google_service_account.sms_gateway
}


import {
  id = "projects/mmontan-ml/locations/us-central1/queues/async-agent-tasks"
  to = google_cloud_tasks_queue.async_agent_tasks
}

import {
  id = "mmontan-ml-agent-artifacts"
  to = google_storage_bucket.artifacts
}

import {
  id = "projects/mmontan-ml/topics/email-notifications"
  to = google_pubsub_topic.email_notifications
}

import {
  id = "projects/mmontan-ml/secrets/twilio-account-sid"
  to = google_secret_manager_secret.secrets["twilio-account-sid"]
}

import {
  id = "projects/mmontan-ml/secrets/twilio-auth-token"
  to = google_secret_manager_secret.secrets["twilio-auth-token"]
}

import {
  id = "projects/mmontan-ml/secrets/twilio-phone-number"
  to = google_secret_manager_secret.secrets["twilio-phone-number"]
}

import {
  id = "projects/mmontan-ml/secrets/google-oauth-client-id"
  to = google_secret_manager_secret.secrets["google-oauth-client-id"]
}

import {
  id = "projects/mmontan-ml/secrets/google-oauth-client-secret"
  to = google_secret_manager_secret.secrets["google-oauth-client-secret"]
}

import {
  id = "projects/mmontan-ml/secrets/oauth-hmac-secret"
  to = google_secret_manager_secret.secrets["oauth-hmac-secret"]
}

import {
  id = "projects/mmontan-ml/secrets/twilio-whatsapp-number"
  to = google_secret_manager_secret.secrets["twilio-whatsapp-number"]
}

import {
  id = "projects/mmontan-ml/secrets/telegram-bot-token"
  to = google_secret_manager_secret.secrets["telegram-bot-token"]
}

import {
  id = "projects/mmontan-ml/secrets/slack-bot-token"
  to = google_secret_manager_secret.secrets["slack-bot-token"]
}

import {
  id = "projects/mmontan-ml/secrets/slack-signing-secret"
  to = google_secret_manager_secret.secrets["slack-signing-secret"]
}
