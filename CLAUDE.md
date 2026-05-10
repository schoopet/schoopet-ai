# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Schoopet is a multi-component project:
1. **Python Agent** (`agents/schoopet/`) - A Google ADK-based multi-agent system:
   - **Personal Agent**: conversational memory, calendar, Drive/Sheets via personal OAuth tokens, email tools, BigQuery/Structured Notes subagent
   - All channels (SMS/WhatsApp/Telegram/Discord/Slack/Email) route to this single agent
   - Search subagent: Real-time Google Search integration
   - Persistent memory via Vertex AI Memory Bank
2. **SMS Gateway** (`sms-gateway/`) - FastAPI service that bridges SMS/Slack/Telegram/Email/Discord to the agent:
   - All channels route to the personal agent
   - Handles OAuth flow for Google APIs (Calendar, Drive/Sheets)
   - Stores tokens in Firestore (access) and Secret Manager (refresh)
   - Feature-based token separation for independent scope authorization
3. **Marketing Website** (`website/`) - A static Vite-based landing page

## Agent Architecture

### Core Components

The agent is built using Google's Agent Development Kit (ADK) with a native multi-agent architecture:

- **root_agent.py**: Single agent via `create_agent()`. `create_adk_agent()` — deploy factory function. Module-level `root_agent = create_agent()` for ADK web dev UI.
- **structured_notes_agent.py**: Structured Notes subagent — BigQuery/MCP. Included in the personal agent.
- **search_agent.py**: Search subagent — real-time Google Search.
- **deploy.py**: Deploys the agent to Vertex AI Agent Engine.
- **agent-engine-cli**: Interactive chat client for deployed remote agent (available on GitHub: [google/agent-engine-cli](https://github.com/google/agent-engine-cli))
- **main.py**: Local development CLI that initializes/updates Agent Engine and runs agent locally
- **memory_config.py**: Configures Vertex AI Memory Bank with custom social_memories topic and managed topics
- **tools/memory_tool.py**: Direct memory management tools for saving and retrieving facts with user-scoped security
- **calendar_tool.py**: Google Calendar integration — `CalendarTool()`, uses personal `feature="calendar"` token
- **drive_sheets_tool.py**: Drive/Sheets tools — `DriveTool()`, `SheetsTool()`, use personal `feature="google-workspace"` token
- **email_tool.py**: Email tools (fetch, list) — uses personal `feature="google"` token
- **oauth_client.py**: Shared OAuth client for agent tools (token retrieval, refresh, authorization link generation)

### Agent Flow

1. Agent Engine initialization or update (creates/updates reasoning engine on Vertex AI)
2. Session creation with Vertex AI-generated session ID
3. Message loop using `Runner.run_async()` with streaming responses
4. Session persistence to memory bank on exit

### Environment Configuration

The project uses a two-layer environment system:

**Layer 1 — `environments/<name>.env`** (project-level, checked into git, no secrets):
```
environments/
  dev.env             ← GOOGLE_CLOUD_PROJECT=schoopet-dev
  prod.env            ← GOOGLE_CLOUD_PROJECT=schoopet-prod
```

Each file contains:
- `GOOGLE_CLOUD_PROJECT` - GCP project ID
- `GOOGLE_CLOUD_LOCATION` - Region (default: us-central1)
- `PERSONAL_AGENT_ENGINE_ID` - Agent reasoning engine ID
- `ARTIFACT_BUCKET_NAME` - GCS bucket for artifacts
- `OAUTH_BASE_URL` / `SMS_GATEWAY_URL` - Service URLs
- `SMS_GATEWAY_URL` / `SMS_GATEWAY_SA` - async task Cloud Tasks target config
- `EMAIL_PUBSUB_TOPIC` - Full Pub/Sub topic name

**Layer 2 — `agents/schoopet/.env`** (secrets + local overrides, gitignored):
- `GOOGLE_SDM_PROJECT_ID` - Google SDM project ID for smart home access
- Any local overrides of layer 1 values

Layer 2 is loaded second and can override layer 1. The deploy scripts handle both layers automatically.

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

### Running Tests

```bash
# Run gateway tests from repo root
make test

# Run individual suite
make test-sms-gateway
```

Always run `make test` after making changes to `sms-gateway/`.

### Agent Development

```bash
# Navigate to agents directory (parent of schoopet)
cd agents

# Install dependencies (requires Python 3.11+)
python -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate
pip install -r schoopet/requirements.txt

# Deploy agent to Vertex AI Agent Engine (engine must already exist — create via Terraform)
./deploy.sh --env=prod           # update existing agent

# Deploy SMS Gateway
./sms-gateway/scripts/deploy.sh --env=prod

# Chat with deployed remote agent (recommended)
# Use the agent-engine-cli (e.g., npx agent-engine-cli chat)
# Connects to remote Agent Engine (no local execution)
# Type 'quit' or 'exit' to save session and terminate

# Run agent locally with CLI (for development/testing)
# Source the env file first so GOOGLE_CLOUD_PROJECT etc. are set
set -a && source environments/prod.env && set +a
cd agents && python -m schoopet.main

# Run ADK web interface for testing
adk web

# Set up BigQuery MCP (required for structured notes)
# 1. Enable BigQuery MCP service:
gcloud beta services mcp enable bigquery.googleapis.com --project=PROJECT_ID
# 2. Ensure you have required IAM roles:
#    - MCP Tool User
#    - BigQuery Job User
#    - BigQuery Data Viewer
```

**Important**: Always use `python -m schoopet.<module>` (not `python <module>.py`) to run agent commands. This maintains the proper Python package context and ensures relative imports work correctly.

**Recommended workflow**:
1. Deploy agent: `./agents/deploy.sh --env=<name>`
2. Chat with deployed agent: Use `agent-engine-cli` (GitHub)
3. Update deployment after code changes: `./agents/deploy.sh --env=<name>`

### Evals

```bash
# Run from repo root
agents/.venv/bin/python -m pytest agents/schoopet/evals/test_eval.py -v

# Eval data files must use .test.json extension
# Personal evals: agents/schoopet/evals/data/personal/  (save_data, async_tasks, search)
# Each subdirectory needs its own test_config.json
```

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

- One engine ID per environment: `PERSONAL_AGENT_ENGINE_ID` in `environments/<name>.env`
- `deploy.sh --env=<name>` reads the ID and updates the existing engine; exits with an error if no ID is set
- New engines must be created via Terraform (`terraform apply`), then copy the printed resource name ID into `environments/<name>.env`
- Updates modify the agent code and memory bank config on the existing engine
- Resource naming: `projects/{project}/locations/{location}/reasoningEngines/{id}`
- `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` are auto-injected by Agent Engine at runtime — no need to include them in `deploy.py` `env_vars`

### Memory Tools

Direct memory management tools for explicit fact storage:
- **save_memory(fact)**: Save single fact to Vertex AI Memory Bank
- **save_multiple_memories(facts)**: Batch save multiple facts
- **retrieve_memories(search_query, top_k)**: Similarity search across stored memories
- Security: All operations require user_id from ToolContext (ADK-injected)
- Fail safely if user_id not available

### Agent Architecture

Single agent handles all channels (SMS/WhatsApp/Telegram/Discord/Slack/Email):

**Personal Agent** (`root_agent.py`):
- Model: `gemini-3-pro-preview` (global endpoint — see GlobalGemini below)
- Conversational memory, calendar, Drive/Sheets, email via user's personal OAuth tokens
- Tools: memory, preferences, calendar, drive, sheets, email, async tasks, search
- `sub_agents=[structured_notes_agent, search_agent]`

**Subagent: Structured Notes**:
- Model: `gemini-3-pro-preview`
- Manages structured, queryable data via BigQuery tables
- Full BigQuery MCP integration (5 tools: list datasets/tables, get info, execute SQL)
- Uses `tools=[..., search_tool]` to enrich structured data with real-time info.
- Connection: Streamable HTTP to https://bigquery.googleapis.com/mcp (MCP protocol, not SSE)

**Tool Agent: Search**:
- Model: `gemini-3-pro-preview` (required for mixed tool support)
- Wrapped as an `AgentTool`.
- Performs real-time Google searches via GoogleSearchTool

**Delegation Flow**:
1. User message arrives on any channel → routed to personal agent.
2. Agent determines the need:
   - For structured data tracking → delegates to **Structured Notes**.
   - For real-time searches → invokes the **Search Agent** tool.
   - For personal memories → handles directly with memory tools.
3. Results returned to the calling agent to be integrated into the response.


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
- `google-workspace`: Drive + Sheets scopes
- `google`: Gmail scopes (email fetch/list)
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

All tools use personal OAuth tokens — no system account tokens.

**CalendarTool** (`calendar_tool.py`):
- `list_calendar_events`, `create_calendar_event`, `update_calendar_event`, `get_calendar_status`
- Uses `feature="calendar"` token for the user.

**DriveTool / SheetsTool** (`drive_sheets_tool.py`):
- `save_attachment_to_drive`, `append_to_sheet`, etc.
- Uses `feature="google-workspace"` token.

**EmailTool** (`email_tool.py`):
- `fetch_email`, `list_emails`
- Uses `feature="google"` token.

**HouseTool** (`house_tool.py`):
- `list_devices()`, `get_device_status(device_name)`
- Uses `feature="house"` for OAuth tokens. Requires `GOOGLE_SDM_PROJECT_ID`.

**Common Behavior**:
- All tools require `user_id` from `ToolContext` (phone number)
- Returns OAuth authorization link when no token exists
- Automatic token refresh via `OAuthClient.get_valid_access_token()`

### Website Structure

- Multi-page static site (index.html, signup.html)
- Vite handles bundling with explicit multi-page config
- Base path set to './' for relative deployment
- Minimal JavaScript (scroll effects only)
- Static assets served from public/ directory

### GlobalGemini — Global Vertex AI Endpoint

`gemini-3-pro-preview` and other `gemini-3.x` preview models are only available on the **global** Vertex AI endpoint (`aiplatform.googleapis.com`), not the regional one (`us-central1-aiplatform.googleapis.com`).

**Pattern**: All agents use `GlobalGemini` (`agents/schoopet/global_gemini.py`), a subclass of ADK's `Gemini` that overrides `api_client` to pass `location='global'` to the genai `Client`. This triggers the genai library to automatically set `https://aiplatform.googleapis.com/` as the base URL.

**Critical pitfalls**:
- Do NOT pass `base_url="https://aiplatform.googleapis.com/"` — the trailing slash prevents the `endswith('.googleapis.com')` check in the genai library, which clears project/location and makes every request go to URL `/` (404).
- Use `model=model_name` (not `model_name=model_name`) — ADK's Gemini Pydantic model has field `model`, not `model_name`. Wrong kwarg is silently ignored, defaulting to `gemini-2.5-flash`.
- Keep `google-adk[eval]` version in `requirements.txt` in sync with Agent Engine runtime to avoid pickle/unpickle AttributeErrors.

### ADK Eval Format

Eval files must use `.test.json` extension (`AgentEvaluator.evaluate()` only scans for that pattern).

**New EvalSet format** (top-level `{eval_set_id, name, eval_cases[]}`):
- `user_content: {role: "user", parts: [{text: "..."}]}`
- `final_response: {role: "model", parts: [{text: "..."}]}`
- `intermediate_data.tool_uses: [{id, name, args, partial_args, will_continue}]`

**Dotenv in evals**: `dotenv.load_dotenv()` searches UP from cwd and misses `agents/schoopet/.env`. Use explicit path:
```python
dotenv.load_dotenv(pathlib.Path(__file__).parent.parent / ".env")
```

### Channel-to-Agent Routing

All channels (SMS, WhatsApp, Telegram, Discord, Slack, Email) route to the single personal agent using `PERSONAL_AGENT_ENGINE_ID`. There is no team agent.
