#!/usr/bin/env python3
"""
Debug tool to manipulate user states in Firestore for the SMS Gateway.
"""
import argparse
import sys
import os
from datetime import datetime, timezone
from google.cloud import firestore

# Add src to path to import models
current_dir = os.path.dirname(os.path.abspath(__file__))
# Go up one level from scripts/ to sms-gateway/ then into src/
src_path = os.path.join(os.path.dirname(current_dir), "src")
sys.path.append(src_path)

try:
    from session.models import SessionDocument
except ImportError as e:
    print(f"Error importing project modules: {e}")
    print(f"sys.path is: {sys.path}")
    sys.exit(1)

COLLECTION_NAME = "sms_sessions"

def get_db():
    return firestore.Client()

def normalize_user_id(phone_number: str) -> str:
    """Normalize phone number for consistent document IDs."""
    return phone_number.lstrip("+").replace("-", "").replace(" ", "")

def get_user(phone_number):
    db = get_db()
    doc_id = normalize_user_id(phone_number)
    doc_ref = db.collection(COLLECTION_NAME).document(doc_id)
    doc = doc_ref.get()
    if doc.exists:
        print(f"User found: {doc_id}")
        data = doc.to_dict()
        # Pretty print key fields
        print("-" * 40)
        print(f"Phone: {data.get('phone_number')}")
        print(f"Opted In: {data.get('opted_in')}")
        print(f"Session ID: {data.get('agent_session_id')}")
        print(f"Last Activity: {data.get('last_activity')}")
        print(f"Message Count: {data.get('message_count')}")
        print("-" * 40)
        print("Full Data:", data)
    else:
        print(f"User {phone_number} (ID: {doc_id}) not found.")

def register_user(phone_number):
    db = get_db()
    doc_id = normalize_user_id(phone_number)
    doc_ref = db.collection(COLLECTION_NAME).document(doc_id)
    
    if doc_ref.get().exists:
        print(f"User {phone_number} already exists.")
        return

    now = datetime.now(timezone.utc)
    
    session_doc = SessionDocument(
        phone_number=phone_number,
        agent_session_id="",
        created_at=now,
        last_activity=now,
        message_count=0,
        opted_in=False,
        opt_in_requested_at=now,
    )
    
    doc_ref.set(session_doc.to_firestore())
    print(f"User {phone_number} registered (opted_in=False).")

def opt_in_user(phone_number, session_id=None):
    db = get_db()
    doc_id = normalize_user_id(phone_number)
    doc_ref = db.collection(COLLECTION_NAME).document(doc_id)
    
    doc = doc_ref.get()
    if not doc.exists:
        print(f"User {phone_number} not found. Register first or use register command.")
        return

    now = datetime.now(timezone.utc)
    update_data = {
        "opted_in": True,
        "last_activity": now,
    }
    
    # Logic to handle session ID
    current_data = doc.to_dict()
    current_session = current_data.get("agent_session_id")
    
    if session_id:
        update_data["agent_session_id"] = session_id
    elif not current_session:
        # Generate a dummy session if none exists and none provided
        dummy_session = f"debug-session-{int(now.timestamp())}"
        update_data["agent_session_id"] = dummy_session
        print(f"Generated dummy session ID: {dummy_session}")
    
    doc_ref.update(update_data)
    print(f"User {phone_number} opted in.")

def opt_out_user(phone_number):
    db = get_db()
    doc_id = normalize_user_id(phone_number)
    doc_ref = db.collection(COLLECTION_NAME).document(doc_id)
    
    if not doc_ref.get().exists:
        print(f"User {phone_number} not found.")
        return

    update_data = {
        "opted_in": False,
        "agent_session_id": "",
        "last_activity": datetime.now(timezone.utc),
    }
    doc_ref.update(update_data)
    print(f"User {phone_number} opted out.")

def delete_user(phone_number):
    db = get_db()
    doc_id = normalize_user_id(phone_number)
    doc_ref = db.collection(COLLECTION_NAME).document(doc_id)
    
    if not doc_ref.get().exists:
        print(f"User {phone_number} not found.")
        return
        
    doc_ref.delete()
    print(f"User {phone_number} deleted.")

def main():
    parser = argparse.ArgumentParser(description="Manage SMS Gateway Users in Firestore")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # Common argument helper
    def add_phone_arg(p):
        p.add_argument("phone_number", help="Phone number (E.164 format preferred)")

    # Get
    parser_get = subparsers.add_parser("get", help="Get user info")
    add_phone_arg(parser_get)

    # Register
    parser_reg = subparsers.add_parser("register", help="Register new user (opted-out)")
    add_phone_arg(parser_reg)

    # Opt-in
    parser_in = subparsers.add_parser("opt-in", help="Opt-in user (activates session)")
    add_phone_arg(parser_in)
    parser_in.add_argument("--session-id", help="Optional Agent Session ID to force")

    # Opt-out
    parser_out = subparsers.add_parser("opt-out", help="Opt-out user (clears session)")
    add_phone_arg(parser_out)

    # Delete
    parser_del = subparsers.add_parser("delete", help="Delete user record")
    add_phone_arg(parser_del)

    args = parser.parse_args()

    try:
        if args.command == "get":
            get_user(args.phone_number)
        elif args.command == "register":
            register_user(args.phone_number)
        elif args.command == "opt-in":
            opt_in_user(args.phone_number, args.session_id)
        elif args.command == "opt-out":
            opt_out_user(args.phone_number)
        elif args.command == "delete":
            delete_user(args.phone_number)
    except Exception as e:
        print(f"Error performing operation: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
