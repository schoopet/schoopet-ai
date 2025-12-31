# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Shoopet is a dual-component project:
1. **Python Agent** (`agents/shoopet/`) - A Google ADK-based multi-agent system with:
   - Main agent: Conversational memory for social interactions
   - Structured Notes subagent: Structured notes management via BigQuery tables
   - Search subagent: Real-time Google Search integration
   - Persistent memory via Vertex AI Memory Bank
2. **Marketing Website** (`website/`) - A static Vite-based landing page

## Agent Architecture

### Core Components

The agent is built using Google's Agent Development Kit (ADK) with a native multi-agent architecture:

- **agent.py**: Defines the main agent (Shoopet) with Gemini LLM and memory tools
  - Handles conversational memory, social interactions, and relationships
  - Delegates to the **Structured Notes** subagent natively via `sub_agents`.
  - Uses the **Search** agent as a tool (via `AgentTool`) for real-time information.
- **structured_notes_agent.py**: Defines the Structured Notes subagent
  - Manages queryable lists and collections via BigQuery tables
  - Full BigQuery MCP integration for SQL operations
  - Has access to the **Search** agent as a tool for data enrichment.
- **search_agent.py**: Defines the Search subagent
  - Performs real-time Google searches using GoogleSearchTool
  - Wrapped as an `AgentTool` to be used by both the Main Agent and Structured Notes Agent.
  - Uses `gemini-2.0-flash-exp` to support mixed tool usage (Search + Functions).
- **main.py**: Entry point that initializes Vertex AI Agent Engine, manages session lifecycle, and provides CLI interface
- **memory_config.py**: Configures Vertex AI Memory Bank with custom social_memories topic and managed topics
- **tools/memory_tool.py**: Direct memory management tools for saving and retrieving facts with user-scoped security

### Agent Flow

1. Agent Engine initialization or update (creates/updates reasoning engine on Vertex AI)
2. Session creation with Vertex AI-generated session ID
3. Message loop using `Runner.run_async()` with streaming responses
4. Session persistence to memory bank on exit

### Environment Configuration

Environment variables can be set in `.env` file (agents/shoopet/.env):
- `GOOGLE_GENAI_USE_VERTEXAI="true"` - Enable Vertex AI backend
- `GOOGLE_CLOUD_PROJECT` - GCP project ID (e.g., "mmontan-ml")
- `GOOGLE_CLOUD_LOCATION` - Region (default: us-central1)
- `AGENT_ENGINE_ID` - Reasoning engine ID (from Vertex AI)

BigQuery MCP is enabled automatically when GOOGLE_CLOUD_PROJECT is set and BigQuery MCP service is enabled:
```bash
gcloud beta services mcp enable bigquery.googleapis.com --project=PROJECT_ID
```

### Package Structure

The agent uses proper Python package structure with relative imports:
- `agents/shoopet/` is the package root
- Must be run with `python -m shoopet.main` to maintain package context
- Uses relative imports (e.g., `from .tools.memory_tool import ...`)
- This ensures code works correctly with both CLI and ADK web interface

## Common Commands

### Agent Development

```bash
# Navigate to agents directory (parent of shoopet)
cd agents

# Install dependencies (requires Python 3.11+)
python -m venv shoopet/.venv
source shoopet/.venv/bin/activate  # On Windows: shoopet\.venv\Scripts\activate
pip install -r shoopet/requirements.txt

# Run agent CLI (IMPORTANT: use python -m to maintain package context)
python -m shoopet.main
# First run creates Agent Engine, subsequent runs update it
# Type 'quit' or 'exit' to save session to memory and terminate

# Run ADK web interface for testing
adk web

# Set up BigQuery MCP (required for structured notes)
# 1. Enable BigQuery MCP service:
gcloud beta services mcp enable bigquery.googleapis.com --project=mmontan-ml
# 2. Ensure you have required IAM roles:
#    - MCP Tool User
#    - BigQuery Job User
#    - BigQuery Data Viewer
```

**Important**: Always use `python -m shoopet.main` (not `python main.py`) to run the agent. This maintains the proper Python package context and ensures relative imports work correctly.

### Website Development

```bash
# Navigate to website directory
cd website

# Install dependencies
npm install

# Development server with hot reload
npm run dev

# Production build (outputs to dist/)
npm run build

# Preview production build
npm run preview
```

## Architecture Notes

### Agent Memory System

The agent uses Vertex AI Native Memory Bank with automatic persistence:
- **Custom topic**: SOCIAL_MEMORIES - tracks people, events, commitments with disambiguation (e.g., "Sarah from work" vs "Sarah the sister")
- **Managed topics**: USER_PERSONAL_INFO, USER_PREFERENCES, KEY_CONVERSATION_DETAILS, EXPLICIT_INSTRUCTIONS
- Model: gemini-2.5-flash for memory generation
- Session saved to memory only on graceful exit (not per-message)
- Direct memory tools (save_memory, retrieve_memories) for explicit fact storage with user_id scoping via ToolContext

### Agent Engine Management

- Agent Engine ID is stored in environment variable and embedded in main.py
- `initialize_agent_engine()` handles both creation and updates
- Updates modify memory bank configuration on existing engine
- Resource naming: `projects/{project}/locations/{location}/reasoningEngines/{id}`

### Memory Tools

Direct memory management tools for explicit fact storage:
- **save_memory(fact)**: Save single fact to Vertex AI Memory Bank
- **save_multiple_memories(facts)**: Batch save multiple facts
- **retrieve_memories(search_query, top_k)**: Similarity search across stored memories
- Security: All operations require user_id from ToolContext (ADK-injected)
- Fail safely if user_id not available

### Multi-Agent Architecture

The system uses a main agent with one native subagent and one shared tool agent:

**Main Agent (Shoopet)**:
- Model: `gemini-3-pro-preview`
- Conversational memory for social interactions and relationships
- Automatic memory bank persistence
- Direct memory tools (save/retrieve)
- Uses `sub_agents=[structured_notes_agent]` for native delegation to BigQuery tasks.
- Uses `tools=[..., search_tool]` to access search capabilities.

**Subagent 1: Structured Notes**:
- Model: `gemini-3-pro-preview`
- Manages structured, queryable data via BigQuery tables
- Full BigQuery MCP integration (5 tools: list datasets/tables, get info, execute SQL)
- Uses `tools=[..., search_tool]` to enrich structured data with real-time info.
- Connection: Streamable HTTP to https://bigquery.googleapis.com/mcp (MCP protocol, not SSE)
- Authentication: Google Cloud credentials (automatic via ADK)

**Tool Agent: Search**:
- Model: `gemini-2.0-flash-exp` (Required for mixed tool support)
- Wrapped as an `AgentTool` for use by other agents.
- Performs real-time Google searches via GoogleSearchTool
- Provides current information, factual lookups, and research.
- Built-in tool that operates internally within Gemini model.

**Delegation Flow**:
1. User makes a request to the main agent.
2. Main agent determines the need:
   - For structured data tracking → delegates natively to **Structured Notes**.
   - For real-time searches → invokes the **Search Agent** tool.
   - For personal memories → handles directly with memory tools.
3. Structured Notes agent can also invoke the **Search Agent** tool if it needs external data (e.g., verifying a restaurant address).
4. Results are returned to the calling agent to be integrated into the response.


**BigQuery Tools Available** (Structured Notes):
- list_datasets: Discover datasets in project
- list_tables: Enumerate tables within datasets
- get_table_schema: Retrieve table schema and row count
- execute_sql: Run SQL queries (CREATE TABLE, INSERT, SELECT, UPDATE, DELETE)

**Search Tools Available** (Search):
- GoogleSearchTool: Built-in Gemini tool that automatically performs Google searches

### Website Structure

- Multi-page static site (index.html, signup.html)
- Vite handles bundling with explicit multi-page config
- Base path set to './' for relative deployment
- Minimal JavaScript (scroll effects only)
- Static assets served from public/ directory
