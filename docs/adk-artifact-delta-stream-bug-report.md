# Bug Report: Agent Engine omits newline separator between streamed events when tool response contains artifact_delta

**Component**: Vertex AI Agent Engine / Google ADK  
**Versions**: `google-adk==1.33.0`, `google-genai==1.73.1`, `google-cloud-aiplatform==1.151.0`  
**Severity**: High — causes silent data loss on every invocation that saves an artifact

---

## Summary

When an ADK tool calls `tool_context.save_artifact()`, the resulting `function_response` event carries a non-empty `artifact_delta` in its `actions` field. Agent Engine serializes this event and the immediately-following model response event to the HTTP stream **without a newline separator** between them, producing concatenated JSON:

```
{...function_response_event...}{...model_response_event...}
```

The `google-genai` streaming parser (`_aiter_response_stream`) reads lines via `aiohttp.content.readline()`, so both objects arrive as a single "line". A downstream `json.loads()` call then raises `JSONDecodeError: Extra data`, aborting the stream and discarding all remaining events including the final model response.

---

## How to Reproduce

### Minimal agent

```python
# agent.py
from google.adk.agents import LlmAgent
from google.adk.tools import FunctionTool
from google.adk.tools.tool_context import ToolContext
from google.genai import types as genai_types

async def save_and_return(tool_context: ToolContext) -> str:
    """Save a small artifact then return a result."""
    part = genai_types.Part(
        inline_data=genai_types.Blob(
            mime_type="image/png",
            # minimal 1x1 PNG
            data=bytes([
                0x89,0x50,0x4e,0x47,0x0d,0x0a,0x1a,0x0a,0x00,0x00,0x00,0x0d,
                0x49,0x48,0x44,0x52,0x00,0x00,0x00,0x01,0x00,0x00,0x00,0x01,
                0x08,0x02,0x00,0x00,0x00,0x90,0x77,0x53,0xde,0x00,0x00,0x00,
                0x0c,0x49,0x44,0x41,0x54,0x08,0xd7,0x63,0xf8,0xcf,0xc0,0x00,
                0x00,0x00,0x02,0x00,0x01,0xe2,0x21,0xbc,0x33,0x00,0x00,0x00,
                0x00,0x49,0x45,0x4e,0x44,0xae,0x42,0x60,0x82,
            ])
        )
    )
    await tool_context.save_artifact("test.png", part)
    return "done"

root_agent = LlmAgent(
    name="repro",
    model="gemini-2.0-flash",
    tools=[FunctionTool(save_and_return)],
    instruction="Call save_and_return, then reply with a one-sentence summary.",
)
```

### Deploy to Agent Engine (requires GCS artifact service)

```python
from vertexai import agent_engines
from vertexai.preview.reasoning_engines import AdkApp
from google.adk.artifacts import GcsArtifactService

app = AdkApp(
    agent=root_agent,
    artifact_service=GcsArtifactService(bucket_name="your-bucket"),
)
remote = agent_engines.create(app, requirements=["google-adk==1.33.0"])
```

### Trigger the bug

```python
import asyncio

async def run():
    session = remote.create_session(user_id="test-user")
    events = []
    async for event in remote.async_stream_query(
        user_id="test-user",
        session_id=session["id"],
        message="Please call save_and_return.",
    ):
        events.append(event)
    print(events)

asyncio.run(run())
```

**Expected**: stream completes, `events` contains function_call, function_response, and final model text events.  
**Actual**: stream raises `UnknownApiResponseError` mid-iteration; final model response is lost.

---

## Error

```
google.genai.errors.UnknownApiResponseError: Failed to parse response as JSON.
Raw response: {"content":{"parts":[{"function_response":{"id":"adk-...","name":"save_and_return","response":{"result":"done"}}}],"role":"user"},"invocation_id":"e-...","author":"repro","actions":{"state_delta":{},"artifact_delta":{"test.png":0},"requested_auth_configs":{},"requested_tool_confirmations":{}},"id":"...","timestamp":...}{"model_version":"gemini-2.0-flash","content":{"parts":[{"text":"I called save_and_return successfully."}],"role":"model"},"finish_reason":"STOP",...}
```

Full traceback:

```
File "...vertexai/_genai/_agent_engines_utils.py", in _method
    async for http_response in self.api_client._async_stream_query(...)
File "...vertexai/_genai/agent_engines.py", in _async_stream_query
    async for response in async_iterator:
File "...google/genai/_api_client.py", in async_generator
    async for chunk in response:
File "...google/genai/_api_client.py", in __anext__
    return await self.segment_iterator.__anext__()
File "...google/genai/_api_client.py", in async_segments
    yield self._load_json_from_response(chunk)
File "...google/genai/_api_client.py", in _load_json_from_response
    raise errors.UnknownApiResponseError(
google.genai.errors.UnknownApiResponseError: Failed to parse response as JSON. Raw response: {event1}{event2}
```

---

## Root Cause

### Agent Engine: Missing newline between consecutive events

Agent Engine serializes ADK events to the HTTP response stream. When a `function_response` event has a **non-empty `artifact_delta`** (caused by a tool calling `tool_context.save_artifact()`), Agent Engine writes that event and the immediately-following model response event to the stream **without a `\n` between them**:

```
# What Agent Engine sends:
{function_response_event_json}{model_response_event_json}\n

# What it should send:
{function_response_event_json}\n{model_response_event_json}\n
```

Events that do **not** involve `artifact_delta` are separated correctly. Only the `artifact_delta`-bearing event triggers the missing newline.

### Why this breaks the client

`_aiter_response_stream` in `google/genai/_api_client.py` reads the stream line-by-line via `aiohttp.content.readline()`. With the missing separator, both events arrive as a single "line". The brace-counting accumulator processes the full line, finds `balance=0` at the end, and yields `"{event1}{event2}"` as one chunk. `_load_json_from_response` then calls `json.loads("{event1}{event2}")`, which raises `JSONDecodeError: Extra data`.

This exception is the observable symptom of the missing newline — it is not a separate bug in the parser. If each event is on its own line, `readline()` returns one complete JSON object per call and parsing succeeds normally.

---

## Impact

- Any agent tool that calls `tool_context.save_artifact()` will silently drop the final model response on every invocation.
- Callers receive an unhandled exception from the streaming iterator; partial event lists (missing the model's text response) are returned.
- This affects both the async streaming path (`async_stream_query`) and the sync path (`stream_query`), which share the same brace-counting logic.

---

## Suggested Fix

The bug is triggered by `artifact_delta` being non-empty. The fix belongs in Agent Engine's `save_artifact` handling: whatever code path processes or serializes an event with a non-empty `artifact_delta` before writing it to the HTTP stream is failing to emit a trailing `\n`. That code path should be investigated and corrected to ensure the newline separator is written unconditionally, regardless of `artifact_delta` content.

---

## Client-Side Workaround

Until the server-side fix is available, callers can recover by catching `UnknownApiResponseError`, extracting the raw payload from the exception message, and splitting it into individual JSON objects using `json.JSONDecoder.raw_decode`:

```python
import json

_RAW_RESPONSE_PREFIX = "Failed to parse response as JSON. Raw response: "

def _split_json_objects(text: str) -> list[dict]:
    decoder = json.JSONDecoder()
    objects: list[dict] = []
    idx = 0
    n = len(text)
    while idx < n:
        while idx < n and text[idx] in " \t\n\r":
            idx += 1
        if idx >= n:
            break
        try:
            obj, end = decoder.raw_decode(text, idx)
            if isinstance(obj, dict):
                objects.append(obj)
            idx = end
        except json.JSONDecodeError:
            break
    return objects

events = []
try:
    async for event in remote.async_stream_query(...):
        events.append(event)
except Exception as exc:
    exc_str = str(exc)
    if _RAW_RESPONSE_PREFIX not in exc_str:
        raise
    raw = exc_str[exc_str.index(_RAW_RESPONSE_PREFIX) + len(_RAW_RESPONSE_PREFIX):]
    recovered = _split_json_objects(raw)
    if not recovered:
        raise
    events.extend(recovered)
```

Note: this workaround depends on the exception message format of `UnknownApiResponseError` remaining stable and should be removed once Agent Engine is fixed.
