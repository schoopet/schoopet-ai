# Shoopet Chat Client

Interactive command-line client for chatting with your deployed Shoopet agent on Vertex AI.

## Features

- ✨ Clean turn-based conversation interface
- 🎨 Color-coded user and agent responses
- 📝 Automatic session management and memory persistence
- 🔄 Streaming responses from the remote agent
- 💾 Session saved to memory on exit

## Quick Start

### From project root:
```bash
./chat.sh
```

### From agents directory:
```bash
cd agents
python -m shoopet.chat
```

## Requirements

The chat client reads configuration from environment variables (`.env` file):

- `GOOGLE_CLOUD_PROJECT` - Your GCP project ID (e.g., "mmontan-ml")
- `GOOGLE_CLOUD_LOCATION` - Region (default: "us-central1")
- `AGENT_ENGINE_ID` - Your deployed agent's ID (from deployment)

## Command-Line Options

```bash
python -m shoopet.chat --help

Options:
  --project PROJECT             Google Cloud Project ID
  --location LOCATION          Google Cloud Region
  --agent-engine-id ID         Deployed Agent Engine ID
  --user-id USER_ID           User identifier (default: "cli-user")
```

## Usage

1. **Start the client:**
   ```bash
   ./chat.sh
   ```

2. **Chat with Shoopet:**
   - Type your message and press Enter
   - Agent responses stream in real-time
   - Each turn is clearly outlined with numbered boxes

3. **Exit gracefully:**
   - Type `quit` or `exit`
   - Or press `Ctrl+C`
   - Session automatically saved to memory

## Example Session

```
╔══════════════════════════════════════════════════════════════╗
║           🐾 Shoopet Chat - Remote Agent Client             ║
╚══════════════════════════════════════════════════════════════╝
Session ID: abc123...
User ID: cli-user
Agent Engine: 172357243746910208

Type your message and press Enter. Type 'quit' or 'exit' to end.

╭─ Turn 1 ─ You ─────────────────────────────────────╮
│ Hi Shoopet! Remember me?
╰────────────────────────────────────────────────────────────╯

╭─ Turn 1 ─ Shoopet ──────────────────────────────╮
│ Of course I remember you! How have you been?
╰────────────────────────────────────────────────────────────╯
```

## How It Works

1. Connects to your deployed Agent Engine on Vertex AI
2. Creates a new session (or resumes existing one)
3. Sends your messages to the remote agent
4. Streams responses back in real-time
5. Saves conversation to memory bank on exit

## Deployment Required

Before using the chat client, you must deploy your agent:

```bash
cd agents
python -m shoopet.deploy
```

This creates (or updates) the Agent Engine on Vertex AI and gives you an `AGENT_ENGINE_ID` to add to your `.env` file.

## Troubleshooting

**Error: "AGENT_ENGINE_ID must be provided"**
- Run `python -m shoopet.deploy` first to create your agent
- Add the ID to your `.env` file

**Error: "GOOGLE_CLOUD_PROJECT not set"**
- Create `.env` file in `agents/shoopet/` directory
- Add: `GOOGLE_CLOUD_PROJECT=your-project-id`

**No response from agent:**
- Check your GCP credentials: `gcloud auth application-default login`
- Verify agent is deployed: `gcloud ai reasoning-engines list --location=us-central1`
