resource "google_firestore_database" "default" {
  project     = var.project_id
  name        = "(default)"
  location_id = var.region
  type        = "FIRESTORE_NATIVE"

  deletion_policy = "ABANDON"

  depends_on = [google_project_service.apis["firestore.googleapis.com"]]


  lifecycle {
    prevent_destroy = true
  }
}

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

  depends_on = [google_firestore_database.default]
}

resource "google_firestore_index" "async_tasks_status_scheduled_at" {
  project    = var.project_id
  collection = "async_tasks"
  database   = "(default)"

  fields {
    field_path = "status"
    order      = "ASCENDING"
  }

  fields {
    field_path = "scheduled_at"
    order      = "ASCENDING"
  }

  depends_on = [google_firestore_database.default]
}

resource "google_firestore_index" "async_tasks_user_status_scheduled" {
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
    field_path = "scheduled_at"
    order      = "ASCENDING"
  }

  depends_on = [google_firestore_database.default]
}
