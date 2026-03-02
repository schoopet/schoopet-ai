"""Evaluation script for the team agent (Slack / Email channels)."""

import pathlib
import sys

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
async def test_team_agent():
    """Run evaluations for the team agent (Slack/Email).

    Covers tools only available in team mode:
    - Email processing (fetch_email, save_file_to_drive, append_to_sheet)
    - Structured Notes via BigQuery subagent
    """
    await AgentEvaluator.evaluate(
        "schoopet.team_agent",  # Module path to the team agent
        str(pathlib.Path(__file__).parent / "data" / "team"),
        num_runs=1,
    )
