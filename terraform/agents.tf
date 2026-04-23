resource "google_vertex_ai_reasoning_engine" "team_agent" {
  provider     = google-beta
  region       = var.region
  display_name = "schoopet-team-agent"

  spec {
    identity_type = "AGENT_IDENTITY"
  }

  lifecycle {
    # spec is fully managed by the agent deploy scripts (deploy.sh / deploy.py).
    # Terraform only creates the engine shell; ignore all spec drift.
    ignore_changes = [spec]
  }
}

resource "google_vertex_ai_reasoning_engine" "personal_agent" {
  provider     = google-beta
  region       = var.region
  display_name = "schoopet-personal-agent"

  spec {
    identity_type = "AGENT_IDENTITY"
  }

  lifecycle {
    # spec is fully managed by the agent deploy scripts (deploy.sh / deploy.py).
    # Terraform only creates the engine shell; ignore all spec drift.
    ignore_changes = [spec]
  }
}
