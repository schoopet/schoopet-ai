"""Cloud Tasks helpers shared by gateway-owned task creation paths."""
import json
import re
from datetime import datetime, timezone
from typing import Optional


DISPATCH_DEADLINE_SECONDS = 900
TASK_NAME_PREFIX = "execute"
TASK_NAME_SUFFIX = "initial"


def normalize_task_id(task_id: str) -> str:
    """Normalize task IDs for deterministic Cloud Tasks resource names."""
    return re.sub(r"[^a-zA-Z0-9-]", "-", task_id).strip("-").lower()


def execute_task_id(task_id: str) -> str:
    """Return the short Cloud Tasks task ID for an async task execution."""
    return f"{TASK_NAME_PREFIX}-{normalize_task_id(task_id)}-{TASK_NAME_SUFFIX}"


def compute_execute_task_name(
    project_id: str,
    location: str,
    queue: str,
    task_id: str,
) -> str:
    """Return the full deterministic Cloud Tasks resource name."""
    return (
        f"projects/{project_id}/locations/{location}"
        f"/queues/{queue}/tasks/{execute_task_id(task_id)}"
    )


def build_execute_task_payload(
    *,
    task_name: str,
    gateway_url: str,
    service_account: Optional[str],
    task_id: str,
    user_id: str,
    schedule_time: Optional[datetime],
):
    """Build the Cloud Tasks wire payload for /internal/tasks/execute."""
    from google.cloud import tasks_v2
    from google.protobuf import duration_pb2, timestamp_pb2

    task = {
        "name": task_name,
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": f"{gateway_url}/internal/tasks/execute",
            "headers": {"Content-Type": "application/json"},
            "body": json.dumps({"task_id": task_id, "user_id": user_id}).encode(),
            "oidc_token": {
                "service_account_email": service_account,
                "audience": gateway_url,
            },
        },
        "dispatch_deadline": duration_pb2.Duration(seconds=DISPATCH_DEADLINE_SECONDS),
    }

    if schedule_time:
        if schedule_time.tzinfo is None:
            schedule_time = schedule_time.replace(tzinfo=timezone.utc)
        timestamp = timestamp_pb2.Timestamp()
        timestamp.FromDatetime(schedule_time)
        task["schedule_time"] = timestamp

    return task
