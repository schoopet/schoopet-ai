"""
Simple chat client for deployed Shoopet Agent Engine.
"""
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


async def main():
    # Get config from environment
    project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
    location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
    agent_engine_id = os.getenv("GOOGLE_CLOUD_AGENT_ENGINE_ID")
    user_id = "cli-user"

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

    print(f"Ready. Session: {session_id}\n")

    # Main loop
    while True:
        user_input = await asyncio.to_thread(input, "You: ")

        if user_input.lower() in ["quit", "exit"]:
            break

        if not user_input.strip():
            continue

        # Query the agent with streaming
        print("Agent:")
        async for event in adk_app.async_stream_query(
            user_id=user_id,
            session_id=session_id,
            message=user_input,
        ):
            print(f"  [{type(event).__name__}] {event}")
        print()


if __name__ == "__main__":
    asyncio.run(main())
