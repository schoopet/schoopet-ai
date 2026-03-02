import os
import sys
import argparse
import logging
import traceback
from pathlib import Path

import dotenv
# Load .env before any package imports so env vars are set when submodules
# (e.g. structured_notes_agent) read GOOGLE_CLOUD_PROJECT at import time.
dotenv.load_dotenv(Path(__file__).parent / ".env")

import vertexai
from vertexai.preview import reasoning_engines
from vertexai import Client, types

from .memory_config import get_memory_bank_config
from .root_agent import create_adk_agent_personal, create_adk_agent_team


def _create_client(project_id: str, location: str, use_agent_identity: bool):
    """Create Vertex AI client with optional agent identity support."""
    if use_agent_identity:
        print("🔐 Agent Identity enabled (requires v1beta1 API)")
        return Client(
            project=project_id,
            location=location,
            http_options=dict(api_version="v1beta1")
        )
    else:
        return Client(project=project_id, location=location)


def _build_config(project_id: str, location: str, staging_bucket: str, requirements_file: str, use_agent_identity: bool, display_name: str = "schoopet-agent-engine"):
    """Build deployment configuration."""
    # Capture current environment variables to pass to the deployed agent
    # Note: Empty values cause deployment errors, so we filter them out
    env_vars_raw = {
        "GOOGLE_GENAI_USE_VERTEXAI": "true",
        "OAUTH_BASE_URL": os.getenv("OAUTH_BASE_URL", "https://api.schoopet.com"),
        # Async Task Configuration
        "ASYNC_TASKS_QUEUE": os.getenv("ASYNC_TASKS_QUEUE", "async-agent-tasks"),
        "TASK_WORKER_URL": os.getenv("TASK_WORKER_URL", ""),
        "TASK_WORKER_SA": os.getenv("TASK_WORKER_SA", ""),
        "SMS_GATEWAY_URL": os.getenv("SMS_GATEWAY_URL", "https://api.schoopet.com"),
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
        "context_spec": {
            "memory_bank_config": memory_config
        }
    }

    if use_agent_identity:
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
        print(f"PERSONAL_AGENT_ENGINE_ID={agent_engine_id}  # if this was a personal agent deploy")
        print(f"TEAM_AGENT_ENGINE_ID={agent_engine_id}       # if this was a team agent deploy")

    # Display effective identity if available
    if remote_agent.api_resource and hasattr(remote_agent.api_resource, 'effective_identity') and remote_agent.api_resource.effective_identity:
        print(f"\n🔐 Effective Identity:")
        print(f"   {remote_agent.api_resource.effective_identity}")

    return agent_engine_id


def deploy(project_id: str, location: str, agent_type: str = "personal", staging_bucket: str = None, agent_engine_id: str = None, use_agent_identity: bool = False):
    """
    Deploys the Schoopet agent to Vertex AI Reasoning Engines using agent object deployment.

    Args:
        project_id: GCP Project ID
        location: GCP Region
        agent_type: "personal" or "team" — which agent to deploy
        staging_bucket: GCS bucket for staging artifacts (optional, will use default if not provided)
        agent_engine_id: Existing Agent Engine ID to update. If None, creates a new one.
        use_agent_identity: Enable Agent Identity for least-privilege access (requires IAM setup)
    """
    if agent_type not in ("personal", "team"):
        print(f"Error: --agent-type must be 'personal' or 'team', got '{agent_type}'")
        return None

    display_name = f"schoopet-{agent_type}-agent"
    print(f"🚀 Starting deployment for project {project_id} in {location} ({agent_type} agent)...")

    # Staging bucket for agent object deployment (SDK handles GCS uploads)
    if not staging_bucket:
        staging_bucket = f"gs://{project_id}-agent-staging"
        print(f"ℹ️  No staging bucket specified, using default: {staging_bucket}")
        print(f"   (Bucket will be created automatically if it doesn't exist)")

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
    print(f"🤖 Creating {agent_type} agent object locally...")
    local_agent = create_adk_agent_personal() if agent_type == "personal" else create_adk_agent_team()
    print("✅ Agent object created successfully")

    # Build configuration
    print("📋 Building deployment configuration...")
    config = _build_config(project_id, location, staging_bucket, requirements_file, use_agent_identity, display_name)

    if agent_engine_id:
        print(f"🔄 Updating existing Agent Engine: {agent_engine_id}")

        # Construct resource name
        if "/" not in agent_engine_id:
             resource_name = f"projects/{project_id}/locations/{location}/reasoningEngines/{agent_engine_id}"
        else:
             resource_name = agent_engine_id

        try:
            # Initialize Client
            client = _create_client(project_id, location, use_agent_identity)

            # Update the existing engine with agent object
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

    else:
        print("✨ Creating NEW Agent Engine...")

        try:
            # Initialize Client
            client = _create_client(project_id, location, use_agent_identity)

            # Create new agent engine from agent object
            print("🚀 Deploying agent object to Vertex AI...")

            remote_agent = client.agent_engines.create(
                agent=local_agent,
                config=config
            )

            print("\n✅ Deployment Successful!")
            return _display_result(remote_agent)

        except Exception as e:
            print(f"\n❌ Deployment Failed: {e}")
            traceback.print_exc()
            return None

def main():
    parser = argparse.ArgumentParser(description="Deploy Schoopet Agent to Vertex AI")
    parser.add_argument("--project", help="Google Cloud Project ID", default=os.getenv("GOOGLE_CLOUD_PROJECT"))
    parser.add_argument("--location", help="Google Cloud Region", default=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"))
    parser.add_argument("--staging-bucket", help="GCS Staging Bucket (optional)", default=os.getenv("GCS_STAGING_BUCKET"))
    parser.add_argument("--agent-type", required=True, choices=["personal", "team"],
                        help="Agent type to deploy: 'personal' (SMS/Telegram) or 'team' (Slack/Email)")
    parser.add_argument("--id", help="Existing Agent Engine ID to update (overrides env vars)", default=None)
    parser.add_argument("--agent-identity", action="store_true", help="Enable Agent Identity for least-privilege access")

    args = parser.parse_args()

    if not args.project:
        print("Error: Project ID must be provided via --project or GOOGLE_CLOUD_PROJECT env var.")
        return

    # Resolve engine ID: --id flag overrides env var, which is agent-type specific
    engine_id = args.id
    if not engine_id:
        if args.agent_type == "personal":
            engine_id = os.getenv("PERSONAL_AGENT_ENGINE_ID") or None
        else:
            engine_id = os.getenv("TEAM_AGENT_ENGINE_ID") or None

    deploy(
        project_id=args.project,
        location=args.location,
        agent_type=args.agent_type,
        staging_bucket=args.staging_bucket,
        agent_engine_id=engine_id,
        use_agent_identity=args.agent_identity
    )

if __name__ == "__main__":
    main()
