#!/usr/bin/env python3
"""Set up Firestore for Schoopet Gateway.

This script:
1. Creates the sms_sessions collection (via a dummy document)
2. Sets up any required indexes

Usage:
    python scripts/setup_firestore.py

Prerequisites:
    - GOOGLE_CLOUD_PROJECT environment variable set
    - Google Cloud credentials configured
"""
import os
import sys
from datetime import datetime

from google.cloud import firestore
from google.cloud.firestore_admin_v1 import FirestoreAdminClient
from google.cloud.firestore_admin_v1.types import Index, Field


def setup_firestore():
    """Set up Firestore collections and indexes."""
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")

    if not project_id:
        print("Error: GOOGLE_CLOUD_PROJECT environment variable is required")
        sys.exit(1)

    print("=" * 50)
    print("Setting up Firestore for Schoopet Gateway")
    print("=" * 50)
    print(f"Project: {project_id}")
    print("=" * 50)

    # Initialize Firestore client
    db = firestore.Client(project=project_id)

    # Create collection by adding a placeholder document
    print("\nCreating sms_sessions collection...")

    collection_ref = db.collection("sms_sessions")

    # Check if collection already has documents
    docs = list(collection_ref.limit(1).stream())

    if docs:
        print("Collection 'sms_sessions' already exists with documents")
    else:
        # Create a placeholder document that we'll delete
        # This ensures the collection appears in the console
        placeholder_ref = collection_ref.document("_placeholder")
        placeholder_ref.set({
            "created_at": datetime.utcnow(),
            "note": "Placeholder document - can be deleted",
        })
        print("Created placeholder document in 'sms_sessions'")

    # Note about indexes
    print("\n" + "=" * 50)
    print("Index Information")
    print("=" * 50)
    print("""
For optimal query performance, you may want to create a composite index:

Collection: sms_sessions
Fields:
  - phone_number (Ascending)
  - last_activity (Descending)

This index supports efficient queries for session lookup and cleanup.

To create via gcloud:

gcloud firestore indexes composite create \\
    --project={project_id} \\
    --collection-group=sms_sessions \\
    --field-config field-path=phone_number,order=ascending \\
    --field-config field-path=last_activity,order=descending

Or create it manually in the Firebase Console:
https://console.firebase.google.com/project/{project_id}/firestore/indexes
""".format(project_id=project_id))

    print("=" * 50)
    print("Firestore Setup Complete!")
    print("=" * 50)
    print("""
Collection created: sms_sessions

Document schema:
  - phone_number: string (E.164 format)
  - personal_agent_session_id: string
  - created_at: timestamp
  - last_activity: timestamp
  - message_count: number

Next steps:
  1. Run ./scripts/setup_secrets.sh to configure gateway secrets
  2. Run ./scripts/deploy.sh to deploy to Cloud Run
""")


if __name__ == "__main__":
    setup_firestore()
