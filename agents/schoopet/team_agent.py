"""Team agent module for ADK eval runner.

Exposes ``root_agent`` as the team-mode agent (Slack/Email) so that
``AgentEvaluator.evaluate("schoopet.team_agent", ...)`` targets the correct
agent instance for team-specific evals.
"""
from .root_agent import create_agent

root_agent = create_agent("team")
agent = root_agent
