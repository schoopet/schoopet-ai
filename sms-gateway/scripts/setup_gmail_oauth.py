"""One-time script to generate the system Gmail OAuth authorization URL.

Usage:
    python scripts/setup_gmail_oauth.py

This prints a URL to open in your browser. After authorizing, the gateway
stores the token under oauth_tokens/email_system_gmail_system in Firestore.

Prerequisites:
    - SMS Gateway deployed and running
    - HMAC secret exists in Secret Manager (oauth-hmac-secret)
    - Google OAuth client configured for gmail.readonly scope
    - pip install google-cloud-secret-manager
"""
import base64
import hashlib
import hmac
import os
import time
from google.cloud import secretmanager

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT", "mmontan-ml")
GATEWAY_URL = os.getenv("GATEWAY_URL", "https://shoopet-sms-gateway-wcwtogbghq-uc.a.run.app")
SYSTEM_PHONE = "email_system"
FEATURE = "gmail_system"


def get_hmac_secret(project_id: str) -> str:
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{project_id}/secrets/oauth-hmac-secret/versions/latest"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("UTF-8")


def generate_token(phone: str, secret: str) -> str:
    expires = int(time.time()) + 600  # 10-minute window
    message = f"{phone}:{expires}"
    signature = hmac.new(
        secret.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()
    return base64.urlsafe_b64encode(f"{message}:{signature}".encode()).decode()


def main():
    print(f"Project:  {PROJECT_ID}")
    print(f"Gateway:  {GATEWAY_URL}")
    print(f"Phone key: {SYSTEM_PHONE}")
    print(f"Feature:  {FEATURE}")
    print()

    print("Fetching HMAC secret from Secret Manager...")
    try:
        secret = get_hmac_secret(PROJECT_ID)
    except Exception as e:
        print(f"ERROR: Could not fetch oauth-hmac-secret: {e}")
        raise SystemExit(1)

    token = generate_token(SYSTEM_PHONE, secret)
    url = f"{GATEWAY_URL}/oauth/google/initiate?token={token}&feature={FEATURE}"

    print("Open this URL in your browser to authorize the system Gmail account:")
    print()
    print(url)
    print()
    print("After authorizing, Firestore will have:")
    print(f"  oauth_tokens/email_system_gmail_system")
    print()
    print("Then run setup_watch to start receiving push notifications:")
    print(f"  curl -H 'Authorization: ...' '{GATEWAY_URL}/internal/email/setup-watch?topic=projects/{PROJECT_ID}/topics/email-notifications'")


if __name__ == "__main__":
    main()
