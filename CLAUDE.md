# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Schoopet is a multi-component project:
1. **Python Agent** (`agents/schoopet/`) - A Google ADK-based multi-agent system with:
   - Main agent: Conversational memory for social interactions
   - Structured Notes subagent: Structured notes management via BigQuery tables
   - Search subagent: Real-time Google Search integration
   - Persistent memory via Vertex AI Memory Bank
   - External API tools: Calendar and Smart Home integration via OAuth
2. **SMS Gateway** (`sms-gateway/`) - FastAPI service that bridges SMS (Twilio) to the agent:
   - Receives SMS messages and routes to Vertex AI Agent Engine
   - Handles OAuth flow for Google APIs (Calendar, Smart Home)
   - Stores tokens in Firestore (access) and Secret Manager (refresh)
   - Feature-based token separation for independent scope authorization
3. **Marketing Website** (`website/`) - A static Vite-based landing page

## Agent Architecture

### Core Components

The agent is built using Google's Agent Development Kit (ADK) with a native multi-agent architecture:

- **agent.py**: Defines the main agent (Schoopet) with Gemini LLM and memory tools
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
  - Uses `gemini-3.1-pro-preview` to support mixed tool usage (Search + Functions).
- **deploy.py**: Deploys agent to Vertex AI Agent Engine (creates or updates remote reasoning engine)
- **agent-engine-cli**: Interactive chat client for deployed remote agent (available on GitHub: [google/agent-engine-cli](https://github.com/google/agent-engine-cli))
- **main.py**: Local development CLI that initializes/updates Agent Engine and runs agent locally
- **memory_config.py**: Configures Vertex AI Memory Bank with custom social_memories topic and managed topics
- **tools/memory_tool.py**: Direct memory management tools for saving and retrieving facts with user-scoped security
- **calendar_tool.py**: Google Calendar integration using OAuth tokens (list/create/update events)
- **house_tool.py**: Google Smart Device Management (SDM) integration for smart home devices
- **oauth_client.py**: Shared OAuth client for agent tools (token retrieval, refresh, authorization link generation)

### Agent Flow

1. Agent Engine initialization or update (creates/updates reasoning engine on Vertex AI)
2. Session creation with Vertex AI-generated session ID
3. Message loop using `Runner.run_async()` with streaming responses
4. Session persistence to memory bank on exit

### Environment Configuration

Environment variables can be set in `.env` file (agents/schoopet/.env):
- `GOOGLE_GENAI_USE_VERTEXAI="true"` - Enable Vertex AI backend
- `GOOGLE_CLOUD_PROJECT` - GCP project ID (e.g., "mmontan-ml")
- `GOOGLE_CLOUD_LOCATION` - Region (default: us-central1)
- `AGENT_ENGINE_ID` - Reasoning engine ID (from Vertex AI)
- `OAUTH_BASE_URL` - SMS Gateway URL for OAuth links (e.g., "https://sms-gateway-xxx.run.app")
- `GOOGLE_SDM_PROJECT_ID` - Google SDM project ID for smart home access (optional)

BigQuery MCP is enabled automatically when GOOGLE_CLOUD_PROJECT is set and BigQuery MCP service is enabled:
```bash
gcloud beta services mcp enable bigquery.googleapis.com --project=PROJECT_ID
```

### Package Structure

The agent uses proper Python package structure with relative imports:
- `agents/schoopet/` is the package root
- Must be run with `python -m schoopet.main` to maintain package context
- Uses relative imports (e.g., `from .tools.memory_tool import ...`)
- This ensures code works correctly with both CLI and ADK web interface

## Common Commands

### Agent Development

```bash
# Navigate to agents directory (parent of schoopet)
cd agents

# Install dependencies (requires Python 3.11+)
python -m venv schoopet/.venv
source schoopet/.venv/bin/activate  # On Windows: schoopet\.venv\Scripts\activate
pip install -r schoopet/requirements.txt

# Deploy agent to Vertex AI Agent Engine (remote)
python -m schoopet.deploy
# First run creates new Agent Engine and outputs ID
# Add ID to .env as AGENT_ENGINE_ID for subsequent updates
# Subsequent runs update the existing Agent Engine

# Chat with deployed remote agent (recommended)
# Use the agent-engine-cli (e.g., npx agent-engine-cli chat)
# Connects to remote Agent Engine (no local execution)
# Type 'quit' or 'exit' to save session and terminate

# Run agent locally with CLI (for development/testing)
python -m schoopet.main
# Runs agent locally but still creates/updates Agent Engine
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

**Important**: Always use `python -m schoopet.<module>` (not `python <module>.py`) to run agent commands. This maintains the proper Python package context and ensures relative imports work correctly.

**Recommended workflow**:
1. Deploy agent once: `python -m schoopet.deploy`
2. Chat with deployed agent: Use `agent-engine-cli` (GitHub)
3. Update deployment after code changes: `python -m schoopet.deploy` (uses existing AGENT_ENGINE_ID)

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

**Main Agent (Schoopet)**:
- Model: `gemini-3.1-pro-preview`
- Conversational memory for social interactions and relationships
- Automatic memory bank persistence
- Direct memory tools (save/retrieve)
- Uses `sub_agents=[structured_notes_agent]` for native delegation to BigQuery tasks.
- Uses `tools=[..., search_tool]` to access search capabilities.

**Subagent 1: Structured Notes**:
- Model: `gemini-3.1-pro-preview`
- Manages structured, queryable data via BigQuery tables
- Full BigQuery MCP integration (5 tools: list datasets/tables, get info, execute SQL)
- Uses `tools=[..., search_tool]` to enrich structured data with real-time info.
- Connection: Streamable HTTP to https://bigquery.googleapis.com/mcp (MCP protocol, not SSE)
- Authentication: Google Cloud credentials (automatic via ADK)

**Tool Agent: Search**:
- Model: `gemini-3.1-pro-preview` (Required for mixed tool support)
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

### OAuth Architecture

The system uses feature-based OAuth token separation to manage different Google API scopes independently:

**Token Storage (per feature)**:
- **Firestore Document ID**: `{normalized_phone}_{feature}` (e.g., `19494136310_calendar`)
- **Secret Manager Key**: `oauth-refresh-{phone}-{feature}` for refresh tokens
- Allows users to authorize calendar access independently from smart home access

**Supported Features**:
- `calendar`: Google Calendar API (`calendar.events` scope)
- `house`: Google Smart Device Management API (`sdm.service` scope)

**OAuth Flow**:
1. Agent tool detects missing token, generates HMAC-signed authorization link
2. User clicks link → SMS Gateway `/oauth/google/initiate?token=...&feature=...`
3. Gateway validates HMAC, redirects to Google OAuth with feature-specific scopes
4. Callback stores tokens with feature tag in Firestore + Secret Manager
5. Agent tools retrieve tokens via `OAuthClient.get_valid_access_token(phone, feature)`

**Key Components**:
- **SMS Gateway (`sms-gateway/src/oauth/`)**: Handles OAuth flow, token storage, refresh
  - `handler.py`: OAuth initiate/callback endpoints
  - `manager.py`: Token exchange, storage, refresh logic
  - `secret_manager.py`: Secure refresh token storage
  - `models.py`: `OAuthState` and `OAuthToken` Pydantic models with `feature` field
- **Agent (`agents/schoopet/oauth_client.py`)**: Shared client for tools
  - Lazy initialization (avoids pickling issues with Firestore/Secret Manager clients)
  - HMAC-signed link generation for secure authorization initiation
  - Automatic token refresh when expired

**Configuration** (`sms-gateway/src/config.py`):
```python
OAUTH_SCOPES = {
    "calendar": ["https://www.googleapis.com/auth/calendar.events", ...],
    "house": ["https://www.googleapis.com/auth/sdm.service", ...],
}
```

### Agent Tools (External APIs)

Tools that access external Google APIs on behalf of users:

**CalendarTool** (`calendar_tool.py`):
- `list_calendar_events(start_date, end_date, max_results)`: List events in date range
- `create_calendar_event(title, start, end, description, location, all_day)`: Create new event
- `update_calendar_event(event_id, ...)`: Update existing event
- `get_calendar_status()`: Check if calendar is connected
- Uses `feature="calendar"` for OAuth tokens

**HouseTool** (`house_tool.py`):
- `list_devices()`: List all connected smart home devices
- `get_device_status(device_name)`: Get status of specific device
- Uses `feature="house"` for OAuth tokens
- Requires `GOOGLE_SDM_PROJECT_ID` environment variable

**Common Behavior**:
- All tools require `user_id` from `ToolContext` (phone number)
- If no valid token exists, returns authorization link for user to click
- Automatic token refresh via `OAuthClient.get_valid_access_token()`

### Website Structure

- Multi-page static site (index.html, signup.html)
- Vite handles bundling with explicit multi-page config
- Base path set to './' for relative deployment
- Minimal JavaScript (scroll effects only)
- Static assets served from public/ directory
