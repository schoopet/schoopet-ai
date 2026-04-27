resource "google_vertex_ai_reasoning_engine" "personal_agent" {
  provider     = google-beta
  region       = var.region
  display_name = "schoopet-personal-agent"

  spec {
    identity_type = "AGENT_IDENTITY"
  }

  lifecycle {
    # The actual engine configuration is managed by the agent deploy scripts
    # (deploy.sh / deploy.py), not by Terraform. After importing a live engine,
    # Terraform must ignore the deployed metadata/config so it does not strip
    # settings such as context_spec or overwrite human-readable labels.
    ignore_changes = [
      description,
      display_name,
      context_spec,
      labels,
      spec,
    ]
  }
}
