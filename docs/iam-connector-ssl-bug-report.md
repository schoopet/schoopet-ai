# Bug Report: GcpAuthProvider SSL Crash After Connection Pool TTL Expiry (~8 minutes)

**Component**: `google-adk[agent-identity]` — `GcpAuthProvider` / `IAMConnectorCredentialsServiceClient`
**Severity**: Critical (all workspace tool calls fail after ~8 minutes of agent uptime)
**Package**: `google-adk==1.26.0` through `1.33.0` (confirmed in prod)
**Reported by**: Mirko Montanari / Schoopet project / 2026-05-18

---

## Summary

`GcpAuthProvider._get_client()` caches a single `IAMConnectorCredentialsServiceClient(transport="rest")` instance. The REST transport creates an HTTP session using urllib3, which (due to `google-auth[pyopenssl]`) uses an `OpenSSL.SSL.Context`. pyopenssl's `Context` becomes immutable after the first `Connection` is created from it. When urllib3's connection pool TTL expires (default ~8 minutes), urllib3 tries to reconfigure the cached `Context` to create a new connection → **crash**.

This causes every workspace tool call (Calendar, Gmail, Drive, etc.) to fail with `RuntimeError: Failed to retrieve credential` after about 8 minutes of agent uptime, producing empty agent responses.

---

## Root Cause

### 1. Import-time pyopenssl injection

`google-auth[pyopenssl]` injects pyopenssl into urllib3 at import time via `google.auth.transport.urllib3`:

```python
# google/auth/transport/urllib3.py (module level)
try:
    import urllib3.contrib.pyopenssl
    urllib3.contrib.pyopenssl.inject_into_urllib3()
except ImportError:
    pass
```

After this, all urllib3 HTTPS connections use `OpenSSL.SSL.Context` instead of stdlib `ssl.SSLContext`.

### 2. Cached client with cached SSL context

`GcpAuthProvider._get_client()` creates and caches the REST client once:

```python
# google/adk/integrations/agent_identity/gcp_auth_provider.py
def _get_client(self) -> Client:
    if self._client is None:
        ...
        self._client = Client(client_options=client_options, transport="rest")
    return self._client  # same client (same HTTP session, same SSL context) forever
```

The first call to `retrieve_credentials` creates an `OpenSSL.SSL.Connection` from the context, making the context immutable.

### 3. Connection pool TTL crash

urllib3's default connection pool TTL is ~360–480 seconds. When a pool entry expires and urllib3 tries to create a new connection, it calls SSL configuration methods on the cached (now immutable) `Context`:

```
ValueError: Context has already been used to create a Connection, it cannot be mutated again
  File "OpenSSL/SSL.py", line 860, in _raise_current_error
  File "OpenSSL/SSL.py", line 1600, in use_certificate
  File ".../urllib3/contrib/pyopenssl.py", line ...
  File ".../google/cloud/iamconnectorcredentials_v1alpha/services/iam_connector_credentials_service/transports/rest.py"
```

### 4. Why global extraction doesn't help

A natural workaround is to call `urllib3.contrib.pyopenssl.extract_from_urllib3()` at startup to revert the injection. However, the Agent Engine's mTLS infrastructure (`configure_mtls_channel` / `_MutualTlsAdapter` in `google.auth.transport.requests`) accesses `ctx_poolmanager._ctx` — a pyopenssl-specific attribute — and crashes with `AttributeError: 'SSLContext' object has no attribute '_ctx'` if pyopenssl is not injected. This breaks **every** GCS artifact load and telemetry channel setup.

---

## Reproduction

Minimum viable reproduction in a Vertex AI Agent Engine (or any env where `google-adk[agent-identity]` is installed):

```python
import asyncio
from google.adk.integrations.agent_identity import GcpAuthProvider, GcpAuthProviderScheme
from google.adk.auth.auth_tool import AuthConfig
from google.adk.auth.credential_manager import CredentialManager

# Force pyopenssl injection (happens automatically on import in normal setups)
import google.auth.transport.urllib3  # noqa: triggers inject_into_urllib3()

provider = GcpAuthProvider()
CredentialManager.register_auth_provider(provider)

# Build a minimal AuthConfig pointing at a real IAM connector
config = AuthConfig(
    auth_scheme=GcpAuthProviderScheme(
        name="projects/YOUR_PROJECT/locations/us-central1/connectors/YOUR_CONNECTOR",
        scopes=["https://www.googleapis.com/auth/userinfo.email", "openid"],
        continue_uri="https://example.com",
    )
)

class FakeContext:
    user_id = "test-user-id"
    function_call_id = None
    session = None

cred_mgr = CredentialManager(auth_config=config)

async def main():
    # First call — succeeds, pyopenssl Connection created, context becomes immutable
    cred = await cred_mgr.get_auth_credential(FakeContext())
    print("First call OK:", cred)
    
    # Simulate connection pool expiry (normally ~8 min, here force it)
    # Access the cached client's underlying session and expire its connections
    client = provider._get_client()
    # In real usage just wait 8-10 minutes; or set the pool TTL low
    
    # Second call on stale pool — crashes
    import time; time.sleep(10)  # replace with real pool TTL wait in a real repro
    cred = await cred_mgr.get_auth_credential(FakeContext())
    print("Second call OK:", cred)

asyncio.run(main())
```

**Faster repro**: Set `urllib3`'s pool timeout to a low value (e.g., 5 seconds) before the first call, then call twice with a sleep in between.

**What you see in logs** (Vertex AI Agent Engine):
```
ValueError: Context has already been used to create a Connection, it cannot be mutated again
  File "/code/.venv/.../OpenSSL/SSL.py", line 860, in _raise_current_error
  File "/code/.venv/.../OpenSSL/SSL.py", line 1600, in use_certificate
  File "/code/.venv/.../urllib3/contrib/pyopenssl.py", line 470, in connect
  File "/code/.venv/.../google/cloud/iamconnectorcredentials_v1alpha/services/iam_connector_credentials_service/transports/rest.py"
  ...
RuntimeError: Failed to retrieve credential for user 'USER_ID' on connector 'CONNECTOR_NAME'
```

---

## Impact

- **All** workspace tool calls (Calendar, Gmail, Drive, Sheets, Docs) fail after ~8 minutes of continuous agent uptime.
- The failure is silent from the user's perspective: the agent returns an empty response.
- Re-deploying the agent resets the timer — the agent works fine for ~8 minutes, then fails again.
- The crash only appears on **reconnects**, not the first call, making it look like an intermittent issue.

---

## Fix

**Option A (Simplest, no caching)**: Change `_get_client()` to return a fresh client on every call:

```python
def _get_client(self) -> Client:
    client_options = None
    if host := os.environ.get("IAM_CONNECTOR_CREDENTIALS_TARGET_HOST"):
        client_options = ClientOptions(api_endpoint=host)
    return Client(client_options=client_options, transport="rest")
```

Each call gets a fresh HTTP session with a fresh `OpenSSL.SSL.Context`. The context is used once, then discarded. No immutability issue. The performance overhead is negligible (IAM connector calls are infrequent, ~once per workspace tool call).

**Option B (Better long-term)**: Use an async gRPC transport once available, which doesn't have the pyopenssl pool issue.

**Option C**: Configure the REST transport's `requests.Session` to use stdlib SSL instead of pyopenssl specifically for the IAM connector, while leaving pyopenssl injected globally for mTLS.

---

## Our Workaround

We monkey-patched `_get_client` at module load time in our `gcp_auth.py`:

```python
from google.adk.integrations.agent_identity import GcpAuthProvider

def _fresh_iam_client(self):
    from google.cloud.iamconnectorcredentials_v1alpha import IAMConnectorCredentialsServiceClient
    from google.api_core.client_options import ClientOptions
    import os
    client_options = None
    if host := os.environ.get("IAM_CONNECTOR_CREDENTIALS_TARGET_HOST"):
        client_options = ClientOptions(api_endpoint=host)
    return IAMConnectorCredentialsServiceClient(client_options=client_options, transport="rest")

GcpAuthProvider._get_client = _fresh_iam_client
```

This is brittle and will break if the method signature changes.

---

## Environment

- Python 3.13 (Vertex AI Agent Engine runtime)
- `google-adk[agent-identity]==1.26.0`–`1.33.0`
- `urllib3==2.x`
- `pyopenssl==24.x`–`26.x`
- `google-auth[pyopenssl]>=2.47`
- `google-cloud-iamconnectorcredentials==0.1.0`
