import asyncio
import os
import sys
import pathlib
from typing import List

# Add the parent directory to sys.path
sys.path.append(str(pathlib.Path(__file__).parent.parent.parent))

from shoopet.root_agent import create_agent
from google.adk.runners import Runner
from google.adk.sessions.local_session_service import LocalSessionService
from google.genai import types
import dotenv

dotenv.load_dotenv()

# Set environment variables for local run if not set
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "true")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", "mmontan-ml")
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", "us-central1")

TEST_PROMPTS = [
    "Hey Shoopet, I'm feeling a bit overwhelmed with my to-do list today. Can you help me pick one thing to start with?",
    "Remind me to call the dentist tomorrow at 10am.",
    "What do you know about my friend Sarah?",
    "I just finished reading 'Atomic Habits'. It was great!",
    "Can you find some good coffee shops near me?",
]

async def run_baseline():
    print("--- SHOOPET PERSONA BASELINE TEST ---")
    
    agent = create_agent()
    session_service = LocalSessionService()
    runner = Runner(
        agent=agent,
        app_name="persona-baseline",
        session_service=session_service
    )
    
    user_id = "test-user"
    session = await session_service.create_session(app_name="persona-baseline", user_id=user_id)
    session_id = session.id
    
    for prompt in TEST_PROMPTS:
        print(f"\nUser: {prompt}")
        message = types.Content(
            role="user",
            parts=[types.Part(text=prompt)]
        )
        
        full_response = ""
        try:
            async for event in runner.run_async(
                user_id=user_id,
                session_id=session_id,
                new_message=message
            ):
                if hasattr(event, "content") and event.content:
                    for part in event.content.parts:
                        if part.text:
                            full_response += part.text
            
            print(f"Agent: {full_response}")
            print(f"Length: {len(full_response)} chars")
            
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(run_baseline())
