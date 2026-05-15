import os
import sys
import argparse
import logging
import traceback
import platform
from pathlib import Path

import dotenv
# Load .env before any package imports so env vars are set when submodules
# (e.g. structured_notes_agent) read GOOGLE_CLOUD_PROJECT at import time.
dotenv.load_dotenv(Path(__file__).parent / ".env")

import vertexai
from vertexai.preview import reasoning_engines
from vertexai import Client, types

from .memory_config import get_memory_bank_config
from .root_agent import create_adk_agent


AGENT_ENGINE_PYTHON_VERSION = "3.13"


def _create_client(project_id: str, location: str):
    """Create Vertex AI client with Agent Identity support."""
    print("🔐 Agent Identity enabled")
    return Client(project=project_id, location=location)


def _build_config(project_id: str, location: str, staging_bucket: str, requirements_file: str, display_name: str = "schoopet-agent-engine"):
    """Build deployment configuration."""
    # Capture current environment variables to pass to the deployed agent
    # Note: Empty values cause deployment errors, so we filter them out
    env_vars_raw = {
        "GOOGLE_GENAI_USE_VERTEXAI": "true",
        "GOOGLE_API_USE_CLIENT_CERTIFICATE": "true",
        "GOOGLE_API_USE_MTLS_ENDPOINT": "always",
        "GOOGLE_API_PREVENT_AGENT_TOKEN_SHARING_FOR_GCP_SERVICES": "false",
        "OAUTH_BASE_URL": os.getenv("OAUTH_BASE_URL", "https://api.schoopet.com"),
        "ARTIFACT_BUCKET_NAME": os.getenv("ARTIFACT_BUCKET_NAME", ""),
        # Async Task Configuration
        "ASYNC_TASKS_QUEUE": os.getenv("ASYNC_TASKS_QUEUE", "async-agent-tasks"),
        "SMS_GATEWAY_URL": os.getenv("SMS_GATEWAY_URL", "https://api.schoopet.com"),
        "SMS_GATEWAY_SA": os.getenv("SMS_GATEWAY_SA", ""),
        # Personal agent display name (falls back to "Schoopet" if unset)
        "PERSONAL_AGENT_NAME": os.getenv("PERSONAL_AGENT_NAME", ""),
        # IAM Connector for 3-legged OAuth
        "IAM_CONNECTOR_GOOGLE_PERSONAL_NAME": os.getenv("IAM_CONNECTOR_GOOGLE_PERSONAL_NAME", ""),
        "IAM_CONNECTOR_CONTINUE_URI": os.getenv("IAM_CONNECTOR_CONTINUE_URI", ""),
        # OpenTelemetry
        "OTEL_SERVICE_NAME": "schoopet-agent",
        "OTEL_PYTHON_LOGGING_AUTO_INSTRUMENTATION_ENABLED": "true",
        "OTEL_SEMCONV_STABILITY_OPT_IN": "gen_ai_latest_experimental",
        "OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT": "EVENT_ONLY",
        "ADK_CAPTURE_MESSAGE_CONTENT_IN_SPANS": "false",
    }
    # Filter out empty values
    env_vars = {k: v for k, v in env_vars_raw.items() if v}

    memory_config = get_memory_bank_config(project_id, location)

    config = {
        "requirements": requirements_file,
        "extra_packages": ["schoopet"],
        "staging_bucket": staging_bucket,
        "agent_framework": "google-adk",
        "env_vars": env_vars,
        "display_name": display_name,
        "description": "Schoopet Agent with Memory and Structured Notes",
        "python_version": AGENT_ENGINE_PYTHON_VERSION,
        "context_spec": {
            "memory_bank_config": memory_config
        }
    }

    config["identity_type"] = types.IdentityType.AGENT_IDENTITY

    return config


def _display_result(remote_agent, agent_engine_id: str = None):
    """Display deployment result including effective identity."""
    if agent_engine_id:
        print(f"Agent Engine ID: {agent_engine_id}")
    elif remote_agent.api_resource and remote_agent.api_resource.name:
        agent_engine_id = remote_agent.api_resource.name.split("/")[-1]
        print(f"Agent Engine ID: {agent_engine_id}")
        print("\n📝 Update your environments/<name>.env file with:")
        print(f"PERSONAL_AGENT_ENGINE_ID={agent_engine_id}")

    # Display effective identity if available
    if remote_agent.api_resource and hasattr(remote_agent.api_resource, 'effective_identity') and remote_agent.api_resource.effective_identity:
        print(f"\n🔐 Effective Identity:")
        print(f"   {remote_agent.api_resource.effective_identity}")

    return agent_engine_id


def deploy(project_id: str, location: str, staging_bucket: str = None, agent_engine_id: str = None):
    """
    Updates an existing agent on Vertex AI Reasoning Engines.

    The agent engine must already exist (created via Terraform). Use PERSONAL_AGENT_ENGINE_ID
    in your environment or pass --id to specify which engine to update.

    Args:
        project_id: GCP Project ID
        location: GCP Region
        staging_bucket: GCS bucket for staging artifacts (optional, will use default if not provided)
        agent_engine_id: Existing Agent Engine ID to update (required).
    """
    if not agent_engine_id:
        print("❌ No agent engine ID provided.")
        print("   Set PERSONAL_AGENT_ENGINE_ID in your environment or pass --id <engine-id>.")
        print("   To create a new engine, use Terraform.")
        sys.exit(1)

    local_python = platform.python_version()
    local_python_minor = ".".join(local_python.split(".")[:2])
    if local_python_minor != AGENT_ENGINE_PYTHON_VERSION:
        print(
            "❌ Incompatible deployment Python version: "
            f"{local_python}. Agent Engine is configured for Python "
            f"{AGENT_ENGINE_PYTHON_VERSION}."
        )
        print(
            "   Deploy from a Python 3.13 virtualenv. Deploying from another "
            "minor version can produce cloudpickle objects that the Agent "
            "Engine runtime cannot load."
        )
        sys.exit(1)

    display_name = "schoopet-agent"
    print(f"🚀 Starting deployment for project {project_id} in {location}...")

    # Staging bucket for agent object deployment (SDK handles GCS uploads)
    if not staging_bucket:
        staging_bucket = f"gs://{project_id}-agent-staging"
        print(f"ℹ️  No staging bucket specified, using default: {staging_bucket}")

    # Initialize Vertex AI
    vertexai.init(
        project=project_id,
        location=location,
        staging_bucket=staging_bucket
    )

    # Path to requirements file
    requirements_file = os.path.join("schoopet", "requirements.txt")
    print(f"📦 Using requirements from: {requirements_file}")

    # Instantiate the agent locally
    print("🤖 Creating agent object locally...")
    local_agent = create_adk_agent()
    print("✅ Agent object created successfully")

    # Build configuration
    print("📋 Building deployment configuration...")
    config = _build_config(project_id, location, staging_bucket, requirements_file, display_name)

    print(f"🔄 Updating existing Agent Engine: {agent_engine_id}")

    # Construct resource name
    if "/" not in agent_engine_id:
        resource_name = f"projects/{project_id}/locations/{location}/reasoningEngines/{agent_engine_id}"
    else:
        resource_name = agent_engine_id

    try:
        client = _create_client(project_id, location)

        print("🚀 Deploying updated agent object...")
        remote_agent = client.agent_engines.update(
            name=resource_name,
            agent=local_agent,
            config=config
        )

        print("\n✅ Update Successful!")
        return _display_result(remote_agent, agent_engine_id)

    except Exception as e:
        print(f"\n❌ Update Failed: {e}")
        traceback.print_exc()
        return None

def main():
    parser = argparse.ArgumentParser(description="Deploy Schoopet Agent to Vertex AI")
    parser.add_argument("--project", help="Google Cloud Project ID", default=os.getenv("GOOGLE_CLOUD_PROJECT"))
    parser.add_argument("--location", help="Google Cloud Region", default=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"))
    parser.add_argument("--staging-bucket", help="GCS Staging Bucket (optional)", default=os.getenv("GCS_STAGING_BUCKET"))
    parser.add_argument("--id", help="Existing Agent Engine ID to update (overrides env vars)", default=None)
    args = parser.parse_args()

    if not args.project:
        print("Error: Project ID must be provided via --project or GOOGLE_CLOUD_PROJECT env var.")
        return

    # Resolve engine ID: --id flag overrides env var
    engine_id = args.id or os.getenv("PERSONAL_AGENT_ENGINE_ID") or None

    deploy(
        project_id=args.project,
        location=args.location,
        staging_bucket=args.staging_bucket,
        agent_engine_id=engine_id,
    )

if __name__ == "__main__":
    main()
