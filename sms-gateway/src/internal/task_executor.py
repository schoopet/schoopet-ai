"""Gateway-owned async task execution.

Cloud Tasks calls the gateway directly. The gateway loads the task, claims it,
runs it in an isolated Agent Engine session, records the result, and posts the
completion to the originating Discord channel.
"""

import logging
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from google.cloud import firestore

from ..discord.routing import parse_channel_tags
from .cloud_tasks import build_execute_task_payload, compute_execute_task_name

logger = logging.getLogger(__name__)

ASYNC_TASKS_COLLECTION = "async_tasks"
ASYNC_TASK_MAX_ATTEMPTS = 5
RESOURCE_CONFIRMED_PREFIX = "_resource_confirmed_"
OFFLINE_MODE_KEY = "_offline_mode"
SCHEDULE_NEXT_RE = re.compile(
    r"(?im)^\s*SCHEDULE_NEXT:\s*(?P<rule>[^|\n]+?)\s*\|\s*(?P<when>\S+)\s*$"
)


def _should_suppress(message: str) -> bool:
    return bool(message) and message.lstrip().startswith("<SUPPRESS RESPONSE>")


class TaskTimeoutError(Exception):
    """Raised when the agent times out; signals Cloud Tasks to retry."""


class TaskTransientError(Exception):
    """Raised on transient upstream errors (e.g. 429); signals Cloud Tasks to retry."""


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
            return {
                "success": True,
                "message": f"Task already in status: {prior_status}",
            }

        # Phase 1: validate, execute, and persist result.
        try:
            self._validate_delivery_target(task)
            prompt = self._build_task_prompt(task)
            result = await self._execute_task(task, prompt)
            await self._schedule_followup_if_requested(task, result)
            await self._update_task_result(task_id, result)
        except (TaskTimeoutError, TaskTransientError) as exc:
            is_transient = isinstance(exc, TaskTransientError)
            kind = "transient error" if is_transient else "timeout"
            attempts = int(task.get("attempts") or 1)
            if attempts >= ASYNC_TASK_MAX_ATTEMPTS:
                error = f"{kind}_exhausted after {attempts} attempts: {exc}"
                logger.warning(
                    "Task %s exhausted retries after %s",
                    task_id,
                    kind,
                )
                await self._update_task_error(task_id, error)
                try:
                    await self._send_completion(task, error=error)
                except Exception:
                    logger.exception(
                        "Failed to send task %s exhaustion notification for %s",
                        kind,
                        task_id,
                    )
                return {"success": False, "error": error}

            logger.warning(
                "Task %s hit %s — resetting to pending for Cloud Tasks retry "
                "(attempt %d/%d)",
                task_id,
                kind,
                attempts,
                ASYNC_TASK_MAX_ATTEMPTS,
            )
            await self._reset_task_to_pending(task_id)
            return {"success": False, "retryable": True, "error": str(exc)}
        except Exception as exc:
            logger.exception(
                "Task %s failed during execution: [%s] %s",
                task_id,
                type(exc).__name__,
                exc,
            )
            error = str(exc)
            await self._update_task_error(task_id, error)
            if gmail_address := task.get("gmail_address"):
                doc_id = gmail_address.lower().replace("@", "_at_").replace(".", "_")
                try:
                    await (
                        self._db.collection("email_state")
                        .document(doc_id)
                        .update({"pending_task_id": firestore.DELETE_FIELD})
                    )
                    logger.info(
                        "Task %s failed: cleared pending_task_id for %s",
                        task_id,
                        gmail_address,
                    )
                except Exception:
                    logger.exception(
                        "Task %s: failed to clear pending_task_id for %s",
                        task_id,
                        gmail_address,
                    )
            try:
                await self._send_completion(task, error=error)
            except Exception:
                logger.exception(
                    "Failed to send task failure notification for %s", task_id
                )
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
        attempts = int(task.get("attempts") or 0) + 1
        logger.info(
            "[firestore] update %s/%s: status=running (was %s) attempts=%d",
            ASYNC_TASKS_COLLECTION,
            task_id,
            status,
            attempts,
        )
        try:
            await doc_ref.update(
                {
                    "status": "running",
                    "started_at": started_at,
                    "attempts": attempts,
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
        task["attempts"] = attempts
        return {"claimed": True, "prior_status": status, "task": task}

    async def _execute_task(self, task: dict[str, Any], prompt: str) -> str:
        user_id = task["user_id"]
        state = self._build_initial_state(task)
        session_id = await self._agent_client.create_session(
            user_id=user_id, state=state
        )
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
                on_tool_call=lambda tool_name: self._record_task_tool_call(
                    task["task_id"],
                    tool_name,
                ),
            )
            # Offline mode: the agent has no user to approve confirmation requests.
            # Auto-reject any pending confirmations so the model gets a follow-up
            # turn and can produce a "needs your approval" message instead of
            # silently returning empty text.
            confirmation_requests = self._agent_client.extract_confirmation_requests(events)
            if confirmation_requests:
                rejections = [
                    (req.function_call_id, False)
                    for req in confirmation_requests
                    if req.function_call_id
                ]
                if rejections:
                    logger.info(
                        "Task %s: auto-rejecting %d confirmation request(s) in offline mode",
                        task.get("task_id"),
                        len(rejections),
                    )
                    events = await self._agent_client.send_confirmation_responses_batch(
                        user_id=user_id,
                        session_id=session_id,
                        confirmations=rejections,
                    )
            result = self._agent_client.extract_text(events)
            if not result:
                error_summary = self._agent_client.last_stream_error_summary
                if error_summary:
                    # Check if all stream errors were transient (retryable).
                    stream_errors = self._agent_client.last_stream_errors
                    from ..agent.client import _RETRYABLE_CODES

                    all_transient = all(
                        err.get("code") in _RETRYABLE_CODES for err in stream_errors
                    )
                    msg = (
                        f"Agent returned an empty response due to upstream error(s): "
                        f"{error_summary}"
                    )
                    if all_transient:
                        raise TaskTransientError(msg)
                    raise RuntimeError(msg)
                raise RuntimeError(
                    "Agent returned an empty response with no stream errors. "
                    "The model may have encountered an unrecoverable error "
                    "(e.g. an unknown tool name). Check Agent Engine logs."
                )
            logger.info("Task execution complete: %s chars", len(result))
            return result
        except TimeoutError as exc:
            raise TaskTimeoutError(
                "Agent did not respond within the configured timeout. "
                "Cloud Tasks will retry."
            ) from exc

    def _build_initial_state(self, task: dict[str, Any]) -> dict[str, Any]:
        state: dict[str, Any] = build_allowed_resource_state(
            task.get("allowed_resource_ids", [])
        )
        state[OFFLINE_MODE_KEY] = True
        state["channel"] = "discord"
        state["task_type"] = "async_task"
        for key in (
            "notification_session_scope",
            "discord_channel_id",
            "discord_channel_name",
        ):
            value = task.get(key)
            if value:
                state[
                    "session_scope" if key == "notification_session_scope" else key
                ] = value
        return state

    async def _update_task_result(self, task_id: str, result: str) -> None:
        logger.info(
            "[firestore] update %s/%s: status=completed result_chars=%d",
            ASYNC_TASKS_COLLECTION,
            task_id,
            len(result),
        )
        await (
            self._db.collection(ASYNC_TASKS_COLLECTION)
            .document(task_id)
            .update(
                {
                    "status": "completed",
                    "result": result,
                    "completed_at": datetime.now(timezone.utc),
                }
            )
        )

    async def _record_task_tool_call(self, task_id: str, tool_name: str) -> None:
        try:
            logger.info(
                "[firestore] update %s/%s: last_tool_call=%s",
                ASYNC_TASKS_COLLECTION,
                task_id,
                tool_name,
            )
            await (
                self._db.collection(ASYNC_TASKS_COLLECTION)
                .document(task_id)
                .update(
                    {
                        "last_event_at": datetime.now(timezone.utc),
                        "last_tool_call": tool_name,
                    }
                )
            )
        except Exception:
            logger.warning(
                "Task %s: failed to record progress for tool call %s",
                task_id,
                tool_name,
                exc_info=True,
            )

    async def _mark_task_notified(self, task_id: str) -> None:
        logger.info(
            "[firestore] update %s/%s: status=notified",
            ASYNC_TASKS_COLLECTION,
            task_id,
        )
        await (
            self._db.collection(ASYNC_TASKS_COLLECTION)
            .document(task_id)
            .update(
                {
                    "status": "notified",
                    "notified_at": datetime.now(timezone.utc),
                }
            )
        )

    async def _update_task_error(self, task_id: str, error: str) -> None:
        logger.warning(
            "[firestore] update %s/%s: status=failed error=%r",
            ASYNC_TASKS_COLLECTION,
            task_id,
            error[:200],
        )
        await (
            self._db.collection(ASYNC_TASKS_COLLECTION)
            .document(task_id)
            .update(
                {
                    "status": "failed",
                    "error": error,
                    "completed_at": datetime.now(timezone.utc),
                }
            )
        )

    async def _reset_task_to_pending(self, task_id: str) -> None:
        logger.info(
            "[firestore] update %s/%s: status=pending (timeout reset for retry)",
            ASYNC_TASKS_COLLECTION,
            task_id,
        )
        await (
            self._db.collection(ASYNC_TASKS_COLLECTION)
            .document(task_id)
            .update(
                {
                    "status": "pending",
                    "started_at": firestore.DELETE_FIELD,
                }
            )
        )

    async def _send_completion(
        self,
        task: dict[str, Any],
        result: Optional[str] = None,
        error: Optional[str] = None,
    ) -> None:
        self._validate_delivery_target(task)

        if error:
            message = f"Your background task encountered an error: {error}"
        else:
            message = result or ""

        if not message:
            logger.warning("Task %s completed with no result", task.get("task_id"))
            return

        if _should_suppress(message):
            logger.info("Task %s: response suppressed", task.get("task_id"))
            return

        routed_blocks, remainder = parse_channel_tags(message)

        for channel_id, content in routed_blocks:
            if content:
                await self._discord_sender.send_channel(channel_id, content)

        if remainder and not _should_suppress(remainder):
            target = task.get("target_channel_id") or task.get("discord_channel_id")
            if target:
                await self._discord_sender.send_channel(target, remainder)
            else:
                await self._discord_sender.send(task["user_id"], remainder)

    def _validate_delivery_target(self, task: dict[str, Any]) -> None:
        if not task.get("discord_channel_id") and not task.get("user_id"):
            raise ValueError("Task is missing both discord_channel_id and user_id")
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

    async def _schedule_followup_if_requested(
        self,
        task: dict[str, Any],
        result: str,
    ) -> None:
        parsed = self._parse_schedule_next(result)
        if not parsed:
            return

        recurrence_rule, scheduled_at = parsed
        followup_id = str(uuid.uuid4())
        doc = {
            "task_id": followup_id,
            "user_id": task["user_id"],
            "task_type": task.get("task_type", "research"),
            "instruction": task["instruction"],
            "context": task.get("context", {}),
            "allowed_resource_ids": task.get("allowed_resource_ids", []),
            "status": "scheduled",
            "scheduled_at": scheduled_at,
            "created_at": datetime.now(timezone.utc),
            "recurrence_rule": recurrence_rule,
            "parent_task_id": task.get("task_id"),
            "notification_session_scope": task.get("notification_session_scope", ""),
            "notification_target_type": task.get("notification_target_type", ""),
            "discord_channel_id": task.get("discord_channel_id", ""),
            "discord_channel_name": task.get("discord_channel_name", ""),
            "target_channel_id": task.get("target_channel_id", ""),
        }

        now = datetime.now(timezone.utc)
        schedule_utc = scheduled_at.astimezone(timezone.utc)
        if schedule_utc <= now + timedelta(days=30):
            cloud_task_name = self._compute_cloud_task_name(followup_id)
            if cloud_task_name:
                doc["cloud_task_name"] = cloud_task_name

        doc_ref = self._db.collection(ASYNC_TASKS_COLLECTION).document(followup_id)
        await doc_ref.set(doc)

        created_name = None
        if doc.get("cloud_task_name"):
            created_name = self._create_cloud_task(
                task_id=followup_id,
                user_id=task["user_id"],
                schedule_time=scheduled_at,
            )
            if not created_name:
                try:
                    await doc_ref.update({"cloud_task_name": firestore.DELETE_FIELD})
                except Exception:
                    logger.warning(
                        "Recurring follow-up task %s: failed to roll back cloud_task_name",
                        followup_id,
                        exc_info=True,
                    )

        logger.info(
            "Task %s scheduled recurring follow-up task %s for %s rule=%r queued=%s",
            task.get("task_id"),
            followup_id,
            scheduled_at.isoformat(),
            recurrence_rule,
            bool(created_name),
        )

    def _parse_schedule_next(self, result: str) -> Optional[tuple[str, datetime]]:
        match = SCHEDULE_NEXT_RE.search(result or "")
        if not match:
            return None

        recurrence_rule = match.group("rule").strip()
        raw_when = match.group("when").strip()
        try:
            scheduled_at = datetime.fromisoformat(raw_when.replace("Z", "+00:00"))
        except ValueError:
            logger.warning("Ignoring invalid SCHEDULE_NEXT datetime: %r", raw_when)
            return None
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=timezone.utc)
        return recurrence_rule, scheduled_at

    async def create_email_batch_task(
        self,
        gmail_address: str,
        user_id: str,
        prompt: str,
        discord_channel_id: str,
        scheduled_at: datetime,
    ) -> str:
        """Create a scheduled async task for email batch processing.

        Returns the task_id. The Cloud Task will call /internal/tasks/execute
        at scheduled_at, which invokes the agent with prompt.
        """
        task_id = str(uuid.uuid4())
        cloud_task_name = self._compute_cloud_task_name(task_id)
        doc = {
            "task_id": task_id,
            "user_id": user_id,
            "task_type": "notification",
            "instruction": prompt,
            "status": "scheduled",
            "scheduled_at": scheduled_at,
            "created_at": datetime.now(timezone.utc),
            "cloud_task_name": cloud_task_name,
            "gmail_address": gmail_address,
        }
        if not cloud_task_name:
            doc.pop("cloud_task_name")
        if discord_channel_id:
            doc["discord_channel_id"] = discord_channel_id
        doc_ref = self._db.collection(ASYNC_TASKS_COLLECTION).document(task_id)
        await doc_ref.set(doc)

        task_name = self._create_cloud_task(
            task_id=task_id,
            user_id=user_id,
            schedule_time=scheduled_at,
        )
        if not task_name:
            logger.warning("Email batch task %s: Cloud Task creation failed", task_id)
            if cloud_task_name:
                try:
                    await doc_ref.update({"cloud_task_name": firestore.DELETE_FIELD})
                except Exception:
                    logger.warning(
                        "Email batch task %s: failed to roll back cloud_task_name",
                        task_id,
                        exc_info=True,
                    )

        return task_id

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
                        await doc_ref.update(
                            {"cloud_task_name": firestore.DELETE_FIELD}
                        )
                    except Exception as cleanup_exc:
                        logger.warning(
                            "Task %s: Cloud Task creation failed and cleanup failed: %s",
                            task_id,
                            cleanup_exc,
                            exc_info=True,
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
        return compute_execute_task_name(project_id, location, queue, task_id)

    def _create_cloud_task(
        self,
        task_id: str,
        user_id: str,
        schedule_time: datetime,
    ) -> Optional[str]:
        from google.api_core.exceptions import AlreadyExists
        from google.cloud import tasks_v2

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
        task_name = compute_execute_task_name(project_id, location, queue, task_id)
        task = build_execute_task_payload(
            task_name=task_name,
            gateway_url=gateway_url,
            service_account=service_account,
            task_id=task_id,
            user_id=user_id,
            schedule_time=schedule_time,
        )

        try:
            return client.create_task(parent=parent, task=task).name
        except AlreadyExists:
            return task_name
        except Exception as exc:
            logger.exception("Failed to create Cloud Task for %s", task_id)
            return None
