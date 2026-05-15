import asyncio
import os
import logging
import sys
import argparse
import traceback
import warnings
from typing import Optional

# Configure detailed logging for debugging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

# Enable httpx detailed logging to see request/response bodies
logging.getLogger("httpx").setLevel(logging.DEBUG)
logging.getLogger("httpx._client").setLevel(logging.DEBUG)
logging.getLogger("mcp").setLevel(logging.DEBUG)
logging.getLogger("mcp.client").setLevel(logging.DEBUG)
logging.getLogger("mcp.client.streamable_http").setLevel(logging.DEBUG)

# Suppress experimental warnings from Vertex AI SDK
try:
    from vertexai._genai.constants import ExperimentalWarning
    warnings.filterwarnings("ignore", category=ExperimentalWarning)
except ImportError:
    pass

# Configure Environment for Vertex AI
os.environ["GOOGLE_GENAI_USE_VERTEXAI"] = "true"

# Enable verbose HTTP logging to see request/response bodies
os.environ["HTTPX_LOG_LEVEL"] = "trace"

from .memory_config import get_memory_service, get_memory_bank_config, save_session_to_memory

# Real ADK Imports
from google.adk.runners import Runner
from google.adk.sessions.vertex_ai_session_service import VertexAiSessionService
from .root_agent import create_agent

from google.genai import types
from vertexai import Client
from vertexai.preview.reasoning_engines import AdkApp

def initialize_agent_engine(
    project_id: str,
    location: str,
    agent_engine_id: Optional[str] = None
) -> Optional[str]:
    """Initializes and deploys the Agent Engine."""
    
    print(f"Initializing Vertex AI for project {project_id} in {location}...")
    client = Client(
        project=project_id,
        location=location,
    )
    
    if agent_engine_id:
        print(f"Updating existing Agent Engine: {agent_engine_id}")
        if "/" not in agent_engine_id:
             resource_name = f"projects/{project_id}/locations/{location}/reasoningEngines/{agent_engine_id}"
        else:
             resource_name = agent_engine_id
             
        try:
            remote_agent = client.agent_engines.update(
                name=resource_name,
                config={
                    "context_spec": {
                        "memory_bank_config": get_memory_bank_config(project_id, location)
                    }
                }
            )
            print(f"Update operation submitted: {remote_agent}")
            return agent_engine_id
        except Exception as e:
            print(f"Failed to update existing agent: {e}")
            return None
    else:
        remote_agent = client.agent_engines.create(
            display_name="sheets-agent-engine",
            config={
                "context_spec": {
                    "memory_bank_config": get_memory_bank_config()
                }
            }
        )
        if remote_agent.api_resource and remote_agent.api_resource.name:
            return remote_agent.api_resource.name.split("/")[-1]
        else:
            return None
    
async def run_agent(project_id: str, location: str, agent_engine_id: str):
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    agent = create_agent()

    # Initialize Memory
    memory_service = get_memory_service()
    
    # Initialize Session Service
    session_service = VertexAiSessionService(
        project=project_id,
        location=location,
        agent_engine_id=agent_engine_id
    )
    
    # Initialize Runner
    runner = Runner(
        agent=agent,
        app_name="adk-agent-cli",
        session_service=session_service,
        memory_service=memory_service
    )

    logger.info("Agent initialized. Ready to run.")
    
    # Simple interaction loop
    print("ADK Agent CLI (Type 'quit' to exit)")
    
    # Create Session (Vertex AI generates the ID)
    user_id = "cli-user-1"
    app_name = "adk-agent-cli"
    
    # Create the session and get the generated ID
    session = await session_service.create_session(
        app_name=agent_engine_id,
        user_id=user_id
    )
    session_id = session.id
    print(f"Created new Vertex AI session: {session_id}")

    while True:
        user_input = await asyncio.to_thread(input, "User: ")
        if user_input.lower() in ["quit", "exit"]:
            await save_session_to_memory(
                session_service=session_service,
                memory_service=memory_service,
                app_name=app_name,
                user_id=user_id,
                session_id=session_id
            )
            break
        
        try:
            # Create content for the user message
            message = types.Content(
                role="user",
                parts=[types.Part(text=user_input)]
            )

            async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=message
            ):
                # Print response text
                if hasattr(event, "content") and event.content:
                     for part in event.content.parts:
                         if part.text:
                             print(f"Agent: {part.text}")
            
            # Continuous saving REMOVED

        except Exception as e:
            logger.error(f"Error during execution: {e}")
            traceback.print_exc()


def main():
    parser = argparse.ArgumentParser(description="ADK Sheets Agent CLI")
    
    parser.parse_args()

    # Read environment variables here
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT")
    location = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")
    agent_engine_id = os.environ.get("GOOGLE_CLOUD_AGENT_ENGINE_ID")
    
    if not project_id:
        print("Error: GOOGLE_CLOUD_PROJECT environment variable must be set.")
        return

    ae_id = initialize_agent_engine(
        project_id=project_id,
        location=location,
        agent_engine_id=agent_engine_id
    )

    if not ae_id:
        print("Failed to initialize agent engine.")
        return

    asyncio.run(run_agent(
        project_id=project_id,
        location=location,
        agent_engine_id=ae_id
    ))


if __name__ == "__main__":
    main()
