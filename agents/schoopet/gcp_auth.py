"""GCP Agent Identity auth provider — registers GcpAuthProvider at import time."""
import os
from google.adk.auth.credential_manager import CredentialManager
from google.adk.auth.auth_tool import AuthConfig
from google.adk.integrations.agent_identity import GcpAuthProvider, GcpAuthProviderScheme

_provider = GcpAuthProvider()
CredentialManager.register_auth_provider(_provider)

GOOGLE_PERSONAL_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/gmail.modify",
    "https://www.googleapis.com/auth/userinfo.email",
    "openid",
]


def build_auth_config() -> AuthConfig:
    return AuthConfig(
        auth_scheme=GcpAuthProviderScheme(
            name=os.getenv("IAM_CONNECTOR_GOOGLE_PERSONAL_NAME", ""),
            scopes=GOOGLE_PERSONAL_SCOPES,
            continue_uri=os.getenv("IAM_CONNECTOR_CONTINUE_URI") or None,
        )
    )


def get_credential_manager() -> CredentialManager:
    return CredentialManager(auth_config=build_auth_config())
