resource "google_firestore_index" "async_tasks_user_status_created" {
  project    = var.project_id
  collection = "async_tasks"
  database   = "(default)"

  fields {
    field_path = "user_id"
    order      = "ASCENDING"
  }

  fields {
    field_path = "status"
    order      = "ASCENDING"
  }

  fields {
    field_path = "created_at"
    order      = "ASCENDING"
  }
}
