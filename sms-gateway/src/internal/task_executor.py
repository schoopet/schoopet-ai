"""Gateway-owned async task execution.

Cloud Tasks calls the gateway directly. The gateway loads the task, claims it,
runs it in an isolated Agent Engine session, records the result, and posts the
completion to the originating Discord channel.
"""
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from google.cloud import firestore

logger = logging.getLogger(__name__)

ASYNC_TASKS_COLLECTION = "async_tasks"
RESOURCE_CONFIRMED_PREFIX = "_resource_confirmed_"


def build_allowed_resource_state(allowed_resource_ids: list[str]) -> dict[str, bool]:
    """Build ADK session state for resources approved at scheduling time."""
    return {
        f"{RESOURCE_CONFIRMED_PREFIX}{resource_id}": True
        for resource_id in allowed_resource_ids
    }


class GatewayTaskExecutor:
    """Executes Firestore async tasks inside the gateway service."""

    def __init__(self, firestore_client, agent_client, discord_sender):
        self._db = firestore_client
        self._agent_client = agent_client
        self._discord_sender = discord_sender

    async def execute_task(self, task_id: str) -> dict[str, Any]:
        claim = await self._claim_task_for_execution(task_id)
        if claim is None:
            return {"success": False, "error": f"Task {task_id} not found"}

        task = claim["task"]
        prior_status = claim["prior_status"]
        if not claim["claimed"]:
            logger.info("Task %s already processed: %s", task_id, prior_status)
            return {"success": True, "message": f"Task already in status: {prior_status}"}

        # Phase 1: validate, execute, and persist result.
        try:
            self._validate_delivery_target(task)
            prompt = self._build_task_prompt(task)
            result = await self._execute_task(task, prompt)
            await self._update_task_result(task_id, result)
        except Exception as exc:
            logger.exception("Task %s failed during execution: %s", task_id, exc)
            error = str(exc)
            await self._update_task_error(task_id, error)
            try:
                await self._send_completion(task, error=error)
            except Exception:
                logger.exception("Failed to send task failure notification for %s", task_id)
            return {"success": False, "error": error}

        # Phase 2: deliver completion notification. Execution already succeeded, so a
        # delivery failure must not overwrite the "completed" status in Firestore.
        try:
            await self._send_completion(task, result=result)
            await self._mark_task_notified(task_id)
        except Exception as exc:
            logger.exception(
                "Task %s completed but notification delivery failed: %s", task_id, exc
            )
        return {"success": True}

    async def _claim_task_for_execution(self, task_id: str) -> Optional[dict[str, Any]]:
        doc_ref = self._db.collection(ASYNC_TASKS_COLLECTION).document(task_id)
        doc = await doc_ref.get()
        if not doc.exists:
            return None

        task = doc.to_dict()
        status = task.get("status")
        if status not in ("pending", "scheduled"):
            return {"claimed": False, "prior_status": status, "task": task}

        started_at = datetime.now(timezone.utc)
        try:
            await doc_ref.update(
                {
                    "status": "running",
                    "started_at": started_at,
                },
                option=firestore.LastUpdateOption(doc.update_time),
            )
        except Exception as exc:
            logger.info("Task %s claim lost due to concurrent update: %s", task_id, exc)
            latest = await doc_ref.get()
            latest_task = latest.to_dict() if latest.exists else task
            latest_status = latest_task.get("status") if latest_task else status
            return {
                "claimed": False,
                "prior_status": latest_status,
                "task": latest_task,
            }

        task["status"] = "running"
        task["started_at"] = started_at
        return {"claimed": True, "prior_status": status, "task": task}

    async def _execute_task(self, task: dict[str, Any], prompt: str) -> str:
        user_id = task["user_id"]
        state = self._build_initial_state(task)
        session_id = await self._agent_client.create_session(user_id=user_id, state=state)
        logger.info(
            "Created background task session %s for task_id=%s user=%s",
            session_id,
            task.get("task_id"),
            user_id,
        )
        logger.info(
            "Offline task prompt for task_id=%s user=%s session_id=%s:\n%s",
            task.get("task_id"),
            user_id,
            session_id,
            prompt,
        )

        try:
            events = await self._agent_client.send_message_events(
                user_id=user_id,
                session_id=session_id,
                message=prompt,
            )
            # In the offline path there is no human to approve confirmations.
            # Auto-decline so the model receives a real API error (e.g. 404 for
            # an invalid resource ID) and can recover on its own.
            confirmations = self._agent_client.extract_confirmation_requests(events)
            if confirmations:
                logger.warning(
                    "Task %s: %d confirmation request(s) in offline mode — auto-declining",
                    task.get("task_id"),
                    len(confirmations),
                )
                follow_up_events: list = []
                for confirmation in confirmations:
                    resource_id = (
                        confirmation.tool_args.get("sheet_id")
                        or confirmation.tool_args.get("document_id")
                        or confirmation.tool_args.get("folder_id")
                        or "unknown"
                    )
                    follow_up_events = await self._agent_client.send_confirmation_response(
                        user_id=user_id,
                        session_id=session_id,
                        confirmation_function_call_id=confirmation.function_call_id,
                        confirmed=False,
                        reason=(
                            f"Resource '{resource_id}' is not in the pre-authorized resource "
                            "list for this task. The write was not performed."
                        ),
                    )
                events = follow_up_events
            result = self._agent_client.extract_text(events)
            if not result:
                raise RuntimeError(
                    "Agent returned an empty response. The model may have encountered "
                    "an unrecoverable error (e.g. an unknown tool name). Check Agent Engine logs."
                )
            logger.info("Task execution complete: %s chars", len(result))
            return result
        except TimeoutError as exc:
            raise RuntimeError(
                "Agent did not respond within the configured timeout. "
                "Check Agent Engine logs for this session."
            ) from exc
        finally:
            await self._agent_client.delete_session(user_id=user_id, session_id=session_id)

    def _build_initial_state(self, task: dict[str, Any]) -> dict[str, Any]:
        state: dict[str, Any] = build_allowed_resource_state(
            task.get("allowed_resource_ids", [])
        )
        state["channel"] = "discord"
        for key in (
            "notification_session_scope",
            "discord_channel_id",
            "discord_channel_name",
        ):
            value = task.get(key)
            if value:
                state["session_scope" if key == "notification_session_scope" else key] = value
        return state

    async def _update_task_result(self, task_id: str, result: str) -> None:
        await self._db.collection(ASYNC_TASKS_COLLECTION).document(task_id).update({
            "status": "completed",
            "result": result,
            "completed_at": datetime.now(timezone.utc),
        })

    async def _mark_task_notified(self, task_id: str) -> None:
        await self._db.collection(ASYNC_TASKS_COLLECTION).document(task_id).update({
            "status": "notified",
            "notified_at": datetime.now(timezone.utc),
        })

    async def _update_task_error(self, task_id: str, error: str) -> None:
        await self._db.collection(ASYNC_TASKS_COLLECTION).document(task_id).update({
            "status": "failed",
            "error": error,
            "completed_at": datetime.now(timezone.utc),
        })

    async def _send_completion(
        self,
        task: dict[str, Any],
        result: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        self._validate_delivery_target(task)

        message = (
            f"Your background task encountered an error: {error}"
            if error
            else (result or "")
        )
        if not message:
            logger.warning("Task %s completed with no result", task.get("task_id"))
            return

        await self._discord_sender.send_channel(task["discord_channel_id"], message)

    def _validate_delivery_target(self, task: dict[str, Any]) -> None:
        if not task.get("discord_channel_id"):
            raise ValueError("Discord task is missing discord_channel_id")
        if not self._discord_sender:
            raise ValueError("Discord sender is not initialized")

    def _build_task_prompt(self, task: dict[str, Any]) -> str:
        parts = [
            f"Execute this {task['task_type']} task:",
            "",
            "Instruction:",
            task["instruction"],
        ]

        if task.get("context"):
            parts.append("")
            parts.append("Additional context:")
            for key, value in task["context"].items():
                parts.append(f"  {key}: {value}")

        parts.append("")
        parts.append(
            "If the instruction asks you to write to Google Docs, Sheets, or Drive, "
            "perform that write with the available tool before finishing. Do not return "
            "the would-be document or sheet content as the final result instead of writing it."
        )
        parts.append(
            "After any requested writes are complete, provide a clear, concise result "
            "that states what was written and where."
        )

        return "\n".join(parts)

    async def requeue_scheduled_tasks(self) -> dict[str, int]:
        """Create Cloud Tasks for scheduled tasks entering the 30-day window."""
        now = datetime.now(timezone.utc)
        window_end = now + timedelta(days=30)

        query = (
            self._db.collection(ASYNC_TASKS_COLLECTION)
            .where("status", "==", "scheduled")
            .where("scheduled_at", "<=", window_end)
        )

        queued = 0
        errors = 0
        async for doc in query.stream():
            data = doc.to_dict()
            if data.get("cloud_task_name"):
                continue

            task_id = data["task_id"]
            scheduled_at = data.get("scheduled_at")
            if not scheduled_at:
                logger.warning("Task %s has no scheduled_at, skipping", task_id)
                errors += 1
                continue

            # Write the deterministic name before creating the Cloud Task so that
            # the executor can read it even if the task fires before this method
            # returns (possible for tasks whose scheduled_at is imminent).
            expected_name = self._compute_cloud_task_name(task_id)
            doc_ref = self._db.collection(ASYNC_TASKS_COLLECTION).document(task_id)
            if expected_name:
                await doc_ref.update({"cloud_task_name": expected_name})

            created_name = self._create_cloud_task(
                task_id=task_id,
                user_id=data["user_id"],
                schedule_time=scheduled_at,
            )
            if created_name:
                queued += 1
            else:
                # Cloud Task creation failed; undo the pre-written name so a
                # future requeue attempt can retry this task.
                if expected_name:
                    try:
                        await doc_ref.update({"cloud_task_name": firestore.DELETE_FIELD})
                    except Exception as cleanup_exc:
                        logger.warning(
                            "Task %s: Cloud Task creation failed and cleanup failed: %s",
                            task_id, cleanup_exc,
                        )
                errors += 1

        return {"queued": queued, "errors": errors}

    def _compute_cloud_task_name(self, task_id: str) -> Optional[str]:
        """Return the deterministic Cloud Task name for task_id without creating it."""
        project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
        if not project_id:
            return None
        location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        queue = os.getenv("ASYNC_TASKS_QUEUE", "async-agent-tasks")
        normalized = re.sub(r"[^a-zA-Z0-9-]", "-", task_id).strip("-").lower()
        return (
            f"projects/{project_id}/locations/{location}"
            f"/queues/{queue}/tasks/execute-{normalized}-initial"
        )

    def _create_cloud_task(
        self,
        task_id: str,
        user_id: str,
        schedule_time: datetime,
    ) -> Optional[str]:
        from google.api_core.exceptions import AlreadyExists
        from google.cloud import tasks_v2
        from google.protobuf import duration_pb2, timestamp_pb2

        project_id = os.getenv("GOOGLE_CLOUD_PROJECT")
        location = os.getenv("GOOGLE_CLOUD_LOCATION", "us-central1")
        queue = os.getenv("ASYNC_TASKS_QUEUE", "async-agent-tasks")
        gateway_url = os.getenv("SMS_GATEWAY_URL")
        service_account = os.getenv(
            "SMS_GATEWAY_SA",
            f"schoopet-sms-gateway@{project_id}.iam.gserviceaccount.com"
            if project_id
            else None,
        )
        if not project_id or not gateway_url:
            logger.error("GOOGLE_CLOUD_PROJECT or SMS_GATEWAY_URL not set")
            return None

        client = tasks_v2.CloudTasksClient()
        parent = client.queue_path(project_id, location, queue)
        normalized = re.sub(r"[^a-zA-Z0-9-]", "-", task_id).strip("-").lower()
        task_name = client.task_path(project_id, location, queue, f"execute-{normalized}-initial")

        if schedule_time.tzinfo is None:
            schedule_time = schedule_time.replace(tzinfo=timezone.utc)
        ts = timestamp_pb2.Timestamp()
        ts.FromDatetime(schedule_time)

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
            "schedule_time": ts,
            "dispatch_deadline": duration_pb2.Duration(seconds=900),
        }

        try:
            return client.create_task(parent=parent, task=task).name
        except AlreadyExists:
            return task_name
        except Exception as exc:
            logger.error("Failed to create Cloud Task for %s: %s", task_id, exc)
            return None
