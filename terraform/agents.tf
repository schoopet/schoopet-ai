resource "google_vertex_ai_reasoning_engine" "team_agent" {
  region       = var.region
  display_name = "schoopet-team-agent"

  spec {
    identity_type = "AGENT_IDENTITY"
  }
}

resource "google_vertex_ai_reasoning_engine" "personal_agent" {
  region       = var.region
  display_name = "schoopet-personal-agent"

  spec {
    identity_type = "AGENT_IDENTITY"
  }
}
