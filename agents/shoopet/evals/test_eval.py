"""Evaluation script for the Structured Notes Agent."""

import pathlib
import sys
import os

# Add the parent directory to sys.path to allow importing the agent
sys.path.append(str(pathlib.Path(__file__).parent.parent))

import dotenv
import pytest
from google.adk.evaluation import AgentEvaluator

pytest_plugins = ("pytest_asyncio",)

@pytest.fixture(scope="session", autouse=True)
def load_env():
    dotenv.load_dotenv()

@pytest.mark.asyncio
async def test_root_agent():
    """Run evaluations for the root agent."""

    await AgentEvaluator.evaluate(
        "shoopet.agent",  # Module path to the agent
        str(pathlib.Path(__file__).parent / "data"),
        num_runs=1,  # Set to 1 for initial testing, increase as needed
    )
