# Schoopet Project Context

## Project Overview

**Schoopet** is a whimsical AI memory assistant service designed to help users with ADHD, reminders, and habit tracking via SMS. The system features a native multi-agent architecture powered by Google Vertex AI, a marketing website, and an SMS gateway.

## Architecture & Components

### 1. Web Frontend (`web/`)
*   **Purpose:** Multi-page landing site and user onboarding.
*   **Tech Stack:** Vanilla HTML, CSS, JavaScript, Vite (Bundler).
*   **Deployment:** Firebase Hosting (via `deploy_web.sh`).
*   **Conventions:** Uses glassmorphism styling and scroll-based animations (`src/main.js`).

### 2. AI Agents (`agents/`)
A sophisticated multi-agent system using Google's Agent Development Kit (ADK):

*   **Main Agent (Shoopet):** Handles conversational memory, social interactions, and relationship building. Uses `gemini-3-pro-preview`.
*   **Structured Notes Subagent:** Manages queryable lists (e.g., shopping lists, book logs) via **BigQuery MCP** integration.
*   **Search Subagent:** Performs real-time Google searches using `gemini-2.0-flash-exp` (wrapped as an `AgentTool`).
*   **Memory System:** Uses Vertex AI Native Memory Bank with automatic persistence.
    *   **Custom Topic:** `SOCIAL_MEMORIES` (tracks people, events, and commitments).
    *   **Managed Topics:** `USER_PERSONAL_INFO`, `USER_PREFERENCES`, etc.

### 3. SMS Gateway (`sms-gateway/`)
*   **Purpose:** Bridges Twilio SMS with the Vertex AI Reasoning Engine.
*   **Tech Stack:** Python, FastAPI, Google Cloud Run.
*   **Key Logic:** Handles SMS segmenting (160 chars), session timeouts (10 min), and webhook security.

## Development & Execution

### Common Commands

**Important:** Always run agent modules from the `agents` directory using the `-m` flag to preserve package context.

*   **Deploy Agent:** `cd agents && python -m shoopet.deploy`
*   **Chat with Remote Agent:** `cd agents && python -m shoopet.chat` (Recommended for testing)
*   **Run Agent Locally:** `cd agents && python -m shoopet.main`
*   **ADK Web Interface:** `cd agents && adk web`
*   **Website Dev:** `cd web && npm run dev`
*   **Website Deploy:** `./deploy_web.sh`

### Environment Variables (`agents/shoopet/.env`)
*   `GOOGLE_CLOUD_PROJECT`: GCP Project ID.
*   `AGENT_ENGINE_ID`: ID of the deployed Vertex AI Reasoning Engine.
*   `GOOGLE_GENAI_USE_VERTEXAI`: Set to `"true"`.

## Infrastructure

*   **Google Cloud Platform:**
    *   **Vertex AI:** Reasoning Engines & Memory Bank.
    *   **Cloud Run:** SMS Gateway hosting.
    *   **BigQuery:** Storage for structured notes (MCP integration).
    *   **Firestore:** SMS session state management.
*   **Firebase:** Web hosting.
*   **Twilio:** SMS entry point.

## Agent Flow
1. **User Input:** Received via SMS or CLI.
2. **Main Agent:** Processes intent; delegates to **Structured Notes** for data tasks or **Search** for factual queries.
3. **Memory Check:** Automatically retrieves relevant user context from Memory Bank.
4. **Tool Use:** Executes BigQuery SQL or Google Search as needed.
5. **Persistence:** Saves session context to Memory Bank upon graceful exit.