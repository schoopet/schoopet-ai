import os
import vertexai
from vertexai import types

PROJECT_ID = os.getenv("GOOGLE_CLOUD_PROJECT")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")

client = vertexai.Client(
    project=PROJECT_ID,
    location=LOCATION,
    http_options=dict(api_version="v1beta1")
)

remote_app = client.agent_engines.create(
    config={"identity_type": types.IdentityType.AGENT_IDENTITY}
)

print(f"Created reasoning engine: {remote_app.api_resource.name}")
if hasattr(remote_app.api_resource, 'effective_identity'):
    print(f"Agent Identity: {remote_app.api_resource.effective_identity}")
