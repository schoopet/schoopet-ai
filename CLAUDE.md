# Repository Guidance

This file is current as of the implementation in this repository. For the full architecture narrative, read [docs/architecture.md](/Users/mmontan/schoopet/docs/architecture.md). For contracts to preserve, read [docs/invariants.md](/Users/mmontan/schoopet/docs/invariants.md).

## Project Shape

Schoopet is a multi-component personal assistant system:

1. `agents/schoopet/` — Google ADK agent deployed to Vertex AI Agent Engine.
2. `sms-gateway/` — FastAPI Cloud Run gateway for Discord, Telegram, Slack, Gmail, OAuth, and internal task execution.
3. `web/` — static Vite site deployed to Firebase Hosting.
4. `terraform/` — infrastructure for Agent Engine, Cloud Run, Cloud Tasks, Scheduler, Pub/Sub, IAM, secrets, and storage.

There is one active reasoning engine per environment: `PERSONAL_AGENT_ENGINE_ID`.

## Agent Notes

The root agent is built in `agents/schoopet/root_agent.py`.

- `create_agent()` returns the ADK `LlmAgent` named `personal`.
- `create_adk_agent()` wraps it in `AdkApp` for Agent Engine deployment.
- Module aliases `root_agent` and `agent` exist for ADK web/dev tooling.
- Default model is currently `gemini-3-flash-preview` through `GlobalGemini`.
- Deployment requires Python 3.13 because cloudpickle/runtime compatibility is enforced in `deploy.py`.

Tools include memory, Calendar, Drive, Docs, Sheets, Gmail, preferences/time, async tasks, task debugging, search, and code execution. Structured notes and deep research are subagents.

Run agent modules from `agents/` with `python -m schoopet.<module>` so package-relative imports work.

## Gateway Notes

The gateway app is `sms-gateway/src/main.py`.

Startup wires:

- Firestore async client
- `AgentEngineClient`
- `SessionManager`
- rate limiter
- channel senders/validators
- OAuth manager
- email services
- gateway-owned task executor

Important runtime facts:

- `SESSION_TIMEOUT_MINUTES` defaults to `30`.
- `AGENT_TIMEOUT_SECONDS` defaults to `900`.
- Session documents live in Firestore collection `sms_sessions`.
- Async task documents live in `async_tasks`.
- OAuth access tokens live in Firestore; refresh tokens live in Secret Manager.
- Internal task execution endpoint is `/internal/tasks/execute`.
- Scheduled task requeue endpoint is `/internal/tasks/requeue-scheduled`.

## Commands

Run gateway tests from repo root:

```bash
make test
```

Run gateway tests directly:

```bash
cd sms-gateway
python -m pytest tests/ -v --tb=short
```

Set up agent dev:

```bash
cd agents
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r schoopet/requirements.txt
set -a && source ../environments/dev.env && set +a
python -m schoopet.main
```

Deploy agent:

```bash
./agents/deploy.sh --env=prod
```

Deploy gateway:

```bash
./sms-gateway/scripts/deploy.sh --env=prod
```

Run web locally:

```bash
cd web
npm install
npm run dev
```

Deploy web:

```bash
./deploy_web.sh
```

## Implementation Constraints

- Preserve user scoping through ADK `tool_context.user_id`.
- Mutating Workspace tools should keep confirmation behavior.
- Resource confirmation key prefix `_resource_confirmed_` must stay in sync between `agents/schoopet/resource_confirmation.py` and `sms-gateway/src/internal/task_executor.py`.
- Gateway changes that affect Agent Engine responses should handle structured ADK events, not just response text.
- Terraform owns Cloud Run service configuration; deploy scripts feed image digests into Terraform.
