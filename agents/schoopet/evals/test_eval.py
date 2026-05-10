"""Evaluation script for the Structured Notes Agent."""

import pathlib
import sys
import os

# Add the agents directory to sys.path to allow importing the schoopet package
sys.path.append(str(pathlib.Path(__file__).parent.parent.parent))

import dotenv
import pytest
from google.adk.evaluation import AgentEvaluator

pytest_plugins = ("pytest_asyncio",)

@pytest.fixture(scope="session", autouse=True)
def load_env():
    env_path = pathlib.Path(__file__).parent.parent / ".env"
    dotenv.load_dotenv(env_path)

@pytest.mark.asyncio
async def test_root_agent():
    """Run evaluations for the personal agent."""

    await AgentEvaluator.evaluate(
        "schoopet.root_agent",  # Module path to the personal agent
        str(pathlib.Path(__file__).parent / "data" / "personal"),
        num_runs=1,
    )
