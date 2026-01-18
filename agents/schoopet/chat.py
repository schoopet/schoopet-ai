"""
Simple chat client for deployed Schoopet Agent Engine.
"""
import argparse
import os
import asyncio
import logging
import warnings

# Enable HTTP body logging
os.environ["HTTPX_LOG_LEVEL"] = "trace"

# Suppress warnings
try:
    from vertexai._genai.constants import ExperimentalWarning
    warnings.filterwarnings("ignore", category=ExperimentalWarning)
except ImportError:
    pass

logging.basicConfig(
    level=logging.DEBUG,
    format='%(levelname)s [%(name)s] %(message)s'
)

# Enable HTTP debugging with request/response bodies
logging.getLogger("httpx").setLevel(logging.DEBUG)
logging.getLogger("httpx._client").setLevel(logging.DEBUG)
logging.getLogger("httpcore").setLevel(logging.DEBUG)
logging.getLogger("httpcore.http11").setLevel(logging.DEBUG)
logging.getLogger("google.auth").setLevel(logging.DEBUG)
logging.getLogger("google.api_core").setLevel(logging.DEBUG)

import vertexai
from google.genai import _api_client

# Monkey patch async_request for non-streaming requests (like create_session)
_original_async_request = _api_client.BaseApiClient.async_request

async def _logged_async_request(self, http_method, path, request_dict, http_options=None):
    print(f"\n{'='*60}")
    print(f"API REQUEST (non-streaming): {http_method.upper()} {path}")
    print(f"Request dict: {request_dict}")
    print(f"{'='*60}\n")

    result = await _original_async_request(self, http_method, path, request_dict, http_options)

    print(f"\n{'='*60}")
    print(f"API RESPONSE: {result}")
    print(f"{'='*60}\n")

    return result

_api_client.BaseApiClient.async_request = _logged_async_request

# Monkey patch async_request_streamed for streaming requests
_original_async_request_streamed = _api_client.BaseApiClient.async_request_streamed

async def _logged_async_request_streamed(self, http_method, path, request_dict, http_options=None):
    print(f"\n{'='*60}")
    print(f"API REQUEST (streaming): {http_method.upper()} {path}")
    print(f"Request dict: {request_dict}")
    print(f"{'='*60}\n")

    result = await _original_async_request_streamed(self, http_method, path, request_dict, http_options)
    return result

_api_client.BaseApiClient.async_request_streamed = _logged_async_request_streamed


def parse_args():
    parser = argparse.ArgumentParser(description="Chat with deployed Schoopet Agent Engine")
    parser.add_argument(
        "--user", "-u",
        default="cli-user",
        help="User ID to impersonate (default: cli-user)"
    )
    return parser.parse_args()


async def main():
    args = parse_args()

    # Get config from environment
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    agent_engine_id = os.getenv("GOOGLE_CLOUD_AGENT_ENGINE_ID")
    user_id = args.user

    if not project_id or not agent_engine_id:
        print("Error: Set GOOGLE_CLOUD_PROJECT and GOOGLE_CLOUD_AGENT_ENGINE_ID")
        return

    # Get agent instance
    client = vertexai.Client(project=project_id, location=location)
    resource_name = f"projects/{project_id}/locations/{location}/reasoningEngines/{agent_engine_id}"
    adk_app = client.agent_engines.get(name=resource_name)

    # Create session
    session = await adk_app.async_create_session(user_id=user_id)
    session_id = session["id"]

    print(f"Ready. User: {user_id}, Session: {session_id}\n")

    # Main loop
    while True:
        user_input = await asyncio.to_thread(input, "You: ")

        if user_input.lower() in ["quit", "exit"]:
            break

        if not user_input.strip():
            continue

        # Query the agent with streaming
        full_response_text = []
        tools_used = []
        
        async for event in adk_app.async_stream_query(
            user_id=user_id,
            session_id=session_id,
            message=user_input,
        ):
            # Preserve original raw printing
            print(f"  [{type(event).__name__}] {event}")

            # Normalize event to access parts for extraction
            parts = []
            if isinstance(event, dict):
                content = event.get("content", {})
                if isinstance(content, dict):
                    parts = content.get("parts", [])
            elif hasattr(event, "content") and event.content:
                if hasattr(event.content, "parts"):
                    parts = event.content.parts

            for part in parts:
                # Extract Tool Usage
                tool_name = None
                if isinstance(part, dict):
                    if "function_call" in part:
                        tool_name = part["function_call"].get("name")
                elif hasattr(part, "function_call") and part.function_call:
                    tool_name = part.function_call.name
                
                if tool_name and tool_name not in tools_used:
                    tools_used.append(tool_name)
                
                # Extract Text
                text = None
                if isinstance(part, dict):
                    text = part.get("text")
                elif hasattr(part, "text"):
                    text = part.text

                if text:
                    full_response_text.append(text)

        # Print extracted information on new lines
        if tools_used:
            print(f"\nTools: {' '.join([f'[{t}]' for t in tools_used])}")
        
        if full_response_text:
            print(f"\nAgent: {''.join(full_response_text)}")
        
        print() # Final newline


if __name__ == "__main__":
    asyncio.run(main())
