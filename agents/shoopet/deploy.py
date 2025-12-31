import os
import sys
import argparse
import logging

import vertexai
from vertexai.preview import reasoning_engines
from vertexai import Client

from memory_config import get_memory_bank_config

def deploy(project_id: str, location: str, staging_bucket: str = None, agent_engine_id: str = None):
    """
    Deploys the Shoopet agent to Vertex AI Reasoning Engines.

    Args:
        project_id: GCP Project ID
        location: GCP Region
        staging_bucket: GCS bucket for staging artifacts
        agent_engine_id: Existing Agent Engine ID to update. If None, creates a new one.
    """

    print(f"🚀 Starting deployment for project {project_id} in {location}...")

    # Ensure staging bucket is set (required for agent deployments)
    if not staging_bucket:
        staging_bucket = f"gs://{project_id}-agent-staging"
        print(f"ℹ️  No staging bucket specified, using default: {staging_bucket}")
        print(f"   (Ensure this bucket exists or create it with: gsutil mb -l {location} {staging_bucket})")

    # Initialize Vertex AI
    vertexai.init(
        project=project_id,
        location=location,
        staging_bucket=staging_bucket
    )

    # Configuration for Memory Bank
    print("📋 Generating Memory Bank configuration...")
    memory_config = get_memory_bank_config(project_id, location)

    # Define source package path (the shoopet directory itself)
    # When packaging shoopet directly, the entrypoint is relative to shoopet's contents
    agents_dir = "shoopet"
    print(f"agents_dir: {agents_dir}")
    source_packages = [agents_dir]

    # Define entrypoint for the agent (absolute imports, no package prefix needed)
    entrypoint_module = "shoopet.root_agent"
    entrypoint_object = "create_agent"

    # Define class methods that the agent engine will expose
    class_methods = [
        {
            "name": "query",
            "api_mode": "structured",
            "parameters": {
                "properties": {
                    "prompt": {"type": "string", "description": "The user's message to the agent"}
                },
                "required": ["prompt"],
                "type": "object"
            }
        }
    ]

    # Path to requirements file
    requirements_file = os.path.join(agents_dir, "requirements.txt")
    print(f"requirements_file: {requirements_file}")

    print(f"📦 Packaging source code from: {source_packages[0]}")
    print(f"   Entrypoint: {entrypoint_module}:{entrypoint_object}")


    if agent_engine_id:
        print(f"🔄 Updating existing Agent Engine with new code: {agent_engine_id}")

        # Construct resource name
        if "/" not in agent_engine_id:
             resource_name = f"projects/{project_id}/locations/{location}/reasoningEngines/{agent_engine_id}"
        else:
             resource_name = agent_engine_id

        try:
            # Initialize Client for Update
            client = Client(project=project_id, location=location)

            # Update the existing engine with new code and config using source files
            print("🚀 Deploying updated code and config from source files...")

            # Environment variables needed by the agent
            # Note: GOOGLE_CLOUD_PROJECT and GOOGLE_CLOUD_LOCATION are automatically provided
            env_vars = {
                "GOOGLE_GENAI_USE_VERTEXAI": "true",
                "AGENT_ENGINE_ID": agent_engine_id
            }

            remote_agent = client.agent_engines.update(
                name=resource_name,
                config={
                    "source_packages": source_packages,
                    "entrypoint_module": entrypoint_module,
                    "entrypoint_object": entrypoint_object,
                    "class_methods": class_methods,
                    "requirements_file": requirements_file,
                    "agent_framework": "google-adk",
                    "env_vars": env_vars,
                    "display_name": "shoopet-agent-engine",
                    "description": "Shoopet Agent with Memory and Structured Notes",
                    "context_spec": {
                        "memory_bank_config": memory_config
                    }
                }
            )

            print("\n✅ Update Successful!")
            print(f"Agent Engine Name: {remote_agent.resource_name}")
            print(f"Agent Engine ID: {agent_engine_id}")

            return agent_engine_id

        except Exception as e:
            print(f"\n❌ Update Failed: {e}")
            import traceback
            traceback.print_exc()
            return None

    else:
        print("✨ Creating NEW Agent Engine...")

        try:
            # Initialize Client for Create
            client = Client(project=project_id, location=location)

            # Create new agent engine from source files
            print("🚀 Deploying to Vertex AI from source files...")

            # Environment variables needed by the agent
            # Note: GOOGLE_CLOUD_PROJECT and GOOGLE_CLOUD_LOCATION are automatically provided
            # AGENT_ENGINE_ID will need to be set after creation via an update
            env_vars = {
                "GOOGLE_GENAI_USE_VERTEXAI": "true"
            }

            remote_agent = client.agent_engines.create(
                config={
                    "source_packages": source_packages,
                    "entrypoint_module": entrypoint_module,
                    "entrypoint_object": entrypoint_object,
                    "class_methods": class_methods,
                    "requirements_file": requirements_file,
                    "agent_framework": "google-adk",
                    "env_vars": env_vars,
                    "display_name": "shoopet-agent-engine",
                    "description": "Shoopet Agent with Memory and Structured Notes",
                    "context_spec": {
                        "memory_bank_config": memory_config
                    }
                }
            )

            print("\n✅ Deployment Successful!")
            print(f"Agent Engine Name: {remote_agent.resource_name}")

            # Extract ID
            ae_id = remote_agent.resource_name.split("/")[-1]
            print(f"Agent Engine ID: {ae_id}")
            print("\n📝 Update your .env file with:")
            print(f"AGENT_ENGINE_ID={ae_id}")

            return ae_id

        except Exception as e:
            print(f"\n❌ Deployment Failed: {e}")
            import traceback
            traceback.print_exc()
            return None

def main():
    parser = argparse.ArgumentParser(description="Deploy Shoopet Agent to Vertex AI")
    parser.add_argument("--project", help="Google Cloud Project ID", default=os.getenv("GOOGLE_CLOUD_PROJECT"))
    parser.add_argument("--location", help="Google Cloud Region", default=os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1"))
    parser.add_argument("--staging-bucket", help="GCS Staging Bucket (optional)", default=os.getenv("GCS_STAGING_BUCKET"))
    parser.add_argument("--id", help="Existing Agent Engine ID to update", default=os.getenv("AGENT_ENGINE_ID"))
    
    args = parser.parse_args()
    
    if not args.project:
        print("Error: Project ID must be provided via --project or GOOGLE_CLOUD_PROJECT env var.")
        return

    deploy(
        project_id=args.project,
        location=args.location,
        staging_bucket=args.staging_bucket,
        agent_engine_id=args.id
    )

if __name__ == "__main__":
    main()
