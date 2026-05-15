"""Diagnostic tool to inspect ADC token validity inside Agent Engine."""
import json
import sys
import urllib.request
from typing import Any

_METADATA_TOKEN_URL = (
    "http://metadata.google.internal/computeMetadata/v1"
    "/instance/service-accounts/default/token"
)
_METADATA_EMAIL_URL = (
    "http://metadata.google.internal/computeMetadata/v1"
    "/instance/service-accounts/default/email"
)


def _fetch_metadata(url: str) -> Any:
    req = urllib.request.Request(url, headers={"Metadata-Flavor": "Google"})
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.read().decode()


def get_adc_token_info() -> dict:
    """Fetch the ADC token from the metadata server and test it against Vertex AI."""
    import os
    result: dict = {}

    # 0. Cert-related env vars and file presence
    result["GOOGLE_API_CERTIFICATE_CONFIG"] = os.getenv("GOOGLE_API_CERTIFICATE_CONFIG", "")
    result["GOOGLE_API_USE_CLIENT_CERTIFICATE"] = os.getenv("GOOGLE_API_USE_CLIENT_CERTIFICATE", "")
    result["GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING"] = os.getenv(
        "GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES", ""
    )
    _WELL_KNOWN_CERT = "/var/run/secrets/workload-spiffe-credentials/certificates.pem"
    _WELL_KNOWN_KEY = "/var/run/secrets/workload-spiffe-credentials/private_key.pem"
    result["cert_file_exists"] = os.path.exists(_WELL_KNOWN_CERT)
    result["key_file_exists"] = os.path.exists(_WELL_KNOWN_KEY)
    cert_config_path = os.getenv("GOOGLE_API_CERTIFICATE_CONFIG", "")
    if cert_config_path and os.path.exists(cert_config_path):
        try:
            with open(cert_config_path) as f:
                result["cert_config_content"] = f.read()[:500]
        except Exception as e:
            result["cert_config_error"] = str(e)
    else:
        # Check default gcloud path
        default_path = os.path.expanduser("~/.config/gcloud/certificate_config.json")
        result["default_cert_config_exists"] = os.path.exists(default_path)

    # 1. Fetch service account email
    try:
        result["sa_email"] = _fetch_metadata(_METADATA_EMAIL_URL).strip()
    except Exception as e:
        result["sa_email_error"] = str(e)

    # 2. Fetch token
    try:
        raw = _fetch_metadata(_METADATA_TOKEN_URL)
        token_data = json.loads(raw)
        token = token_data.get("access_token", "")
        result["token_type"] = token_data.get("token_type")
        result["expires_in_seconds"] = token_data.get("expires_in")
        result["token"] = token
    except Exception as e:
        result["token_error"] = str(e)
        return result

    # 3. Test the token against a simple Vertex AI endpoint
    import os
    project = os.getenv("GOOGLE_CLOUD_PROJECT", "")
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    engine_id = os.getenv("PERSONAL_AGENT_ENGINE_ID", "")
    if project and engine_id:
        test_url = (
            f"https://{location}-aiplatform.googleapis.com/v1beta1"
            f"/projects/{project}/locations/{location}"
            f"/reasoningEngines/{engine_id}/sessions"
        )
        try:
            req = urllib.request.Request(
                test_url,
                data=b'{"userId":"adc-test"}',
                headers={
                    "Authorization": f"Bearer {token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                result["sessions_api_test"] = "OK"
                result["sessions_api_status"] = resp.status
        except urllib.error.HTTPError as e:
            result["sessions_api_test"] = f"HTTP {e.code}"
            result["sessions_api_error"] = e.read().decode()[:300]
        except Exception as e:
            result["sessions_api_test"] = f"ERROR: {e}"
    else:
        result["sessions_api_test"] = "skipped — missing project or engine_id"

    return result


# Log token status at import time when running in Agent Engine
import os as _os
if _os.getenv("GOOGLE_CLOUD_AGENT_ENGINE_ID"):
    try:
        _info = get_adc_token_info()
        print(
            f"[ADC-DIAG] sa={_info.get('sa_email')} "
            f"token={_info.get('token')} "
            f"expires_in={_info.get('expires_in_seconds')} "
            f"sessions_test={_info.get('sessions_api_test')} "
            f"CERT_CONFIG_ENV={_info.get('GOOGLE_API_CERTIFICATE_CONFIG')!r} "
            f"cert_file_exists={_info.get('cert_file_exists')} "
            f"key_file_exists={_info.get('key_file_exists')} "
            f"default_cert_config_exists={_info.get('default_cert_config_exists')} "
            f"cert_config_content={_info.get('cert_config_content')!r}",
            file=sys.stderr, flush=True,
        )
    except Exception as _e:
        print(f"[ADC-DIAG] startup check failed: {_e}", file=sys.stderr, flush=True)
