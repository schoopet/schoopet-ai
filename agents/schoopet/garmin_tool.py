"""Garmin Connect tool for the agent using the python-garminconnect library."""
import asyncio
import os
from datetime import date, datetime, timezone
from typing import Optional

from google.adk.tools import ToolContext

from .utils import require_user_id

_GARMIN_TOKENS_COLLECTION = "garmin_tokens"
_TOKEN_DOC_ID = "owner"


class GarminTool:
    """Access Garmin Connect health and fitness data via the unofficial API.

    Authentication uses Garmin's mobile SSO flow (same as the Android app),
    so no developer account is needed. Credentials are read from GARMIN_EMAIL
    and GARMIN_PASSWORD environment variables. OAuth tokens are persisted in
    Firestore and refreshed automatically; a full re-login is only needed when
    the refresh token expires or is revoked.

    Note: If the Garmin account has MFA enabled, run the one-time setup script
    (scripts/garmin_setup.py) locally to bootstrap the token into Firestore
    before using these tools.
    """

    def __init__(self):
        self._client = None
        self._firestore_client = None

    # ── Firestore helpers ──────────────────────────────────────────────────

    def _get_firestore(self):
        if self._firestore_client is None:
            project = os.getenv("GOOGLE_CLOUD_PROJECT")
            if project:
                from google.cloud import firestore
                self._firestore_client = firestore.Client(project=project)
        return self._firestore_client

    def _load_tokens(self) -> Optional[str]:
        db = self._get_firestore()
        if not db:
            return None
        doc = db.collection(_GARMIN_TOKENS_COLLECTION).document(_TOKEN_DOC_ID).get()
        return doc.to_dict().get("tokens") if doc.exists else None

    def _save_tokens(self, tokens: str) -> None:
        db = self._get_firestore()
        if not db:
            return
        db.collection(_GARMIN_TOKENS_COLLECTION).document(_TOKEN_DOC_ID).set(
            {"tokens": tokens, "updated_at": datetime.now(timezone.utc)},
        )

    # ── Auth helpers ───────────────────────────────────────────────────────

    def _build_client(self, force_login: bool = False):
        """Build an authenticated Garmin client.

        Loads saved tokens from Firestore when available. If force_login=True or
        no tokens are found, performs a full username/password login and saves
        the resulting tokens back to Firestore.
        """
        from garminconnect import Garmin

        email = os.getenv("GARMIN_EMAIL")
        password = os.getenv("GARMIN_PASSWORD")

        if not force_login:
            token_str = self._load_tokens()
            if token_str:
                client = Garmin(email=email, password=password)
                client.garth.loads(token_str)
                return client

        client = Garmin(email=email, password=password)
        client.login()
        self._save_tokens(client.garth.dumps())
        return client

    async def _get_client(self, force_login: bool = False):
        if self._client is None or force_login:
            self._client = await asyncio.to_thread(self._build_client, force_login)
        return self._client

    async def _call(self, method_name: str, *args, **kwargs):
        """Invoke a synchronous Garmin API method in a thread pool.

        Re-authenticates once if the access token has expired.
        """
        from garminconnect import GarminConnectAuthenticationError

        try:
            client = await self._get_client()
            return await asyncio.to_thread(getattr(client, method_name), *args, **kwargs)
        except GarminConnectAuthenticationError:
            client = await self._get_client(force_login=True)
            return await asyncio.to_thread(getattr(client, method_name), *args, **kwargs)

    # ── Tool methods ───────────────────────────────────────────────────────

    async def get_today_stats(self, tool_context: ToolContext = None) -> str:
        """Get today's activity summary: steps, calories, intensity minutes, floors, distance."""
        _, err = require_user_id(tool_context, "garmin")
        if err:
            return err

        today = date.today().isoformat()
        try:
            data = await self._call("get_stats", today)
            if not data:
                return "No stats available for today."
            steps = data.get("totalSteps", "N/A")
            total_cal = data.get("totalKilocalories", "N/A")
            active_cal = data.get("activeKilocalories", "N/A")
            floors = data.get("floorsAscended", "N/A")
            distance_m = data.get("totalDistanceMeters")
            distance = f"{distance_m / 1000:.2f} km" if distance_m else "N/A"
            intensity = (
                data.get("moderateIntensityMinutes", 0)
                + data.get("vigorousIntensityMinutes", 0) * 2
            )
            return (
                f"Today's stats ({today}):\n"
                f"  Steps: {steps}\n"
                f"  Distance: {distance}\n"
                f"  Calories: {total_cal} kcal (active: {active_cal} kcal)\n"
                f"  Floors: {floors}\n"
                f"  Intensity minutes: {intensity}"
            )
        except Exception as e:
            return f"Error fetching today's stats: {e}"

    async def get_sleep_data(
        self,
        sleep_date: str = None,
        tool_context: ToolContext = None,
    ) -> str:
        """Get sleep data for a given date (YYYY-MM-DD). Defaults to last night."""
        _, err = require_user_id(tool_context, "garmin")
        if err:
            return err

        cdate = sleep_date or date.today().isoformat()
        try:
            data = await self._call("get_sleep_data", cdate)
            if not data:
                return f"No sleep data available for {cdate}."
            daily = data.get("dailySleepDTO", {})

            def _fmt_secs(s):
                if not s:
                    return "N/A"
                return f"{s // 3600}h {(s % 3600) // 60}m"

            score = daily.get("sleepScores", {}).get("overall", {}).get("value", "N/A")
            duration = _fmt_secs(daily.get("sleepTimeSeconds"))
            deep = _fmt_secs(daily.get("deepSleepSeconds"))
            light = _fmt_secs(daily.get("lightSleepSeconds"))
            rem = _fmt_secs(daily.get("remSleepSeconds"))
            awake = _fmt_secs(daily.get("awakeSleepSeconds"))
            return (
                f"Sleep ({cdate}):\n"
                f"  Score: {score}/100\n"
                f"  Total: {duration}\n"
                f"  Deep: {deep}  REM: {rem}  Light: {light}  Awake: {awake}"
            )
        except Exception as e:
            return f"Error fetching sleep data: {e}"

    async def get_body_battery(
        self,
        body_battery_date: str = None,
        tool_context: ToolContext = None,
    ) -> str:
        """Get Body Battery energy levels for a given date (YYYY-MM-DD). Defaults to today."""
        _, err = require_user_id(tool_context, "garmin")
        if err:
            return err

        cdate = body_battery_date or date.today().isoformat()
        try:
            data = await self._call("get_body_battery", cdate, cdate)
            if not data:
                return f"No Body Battery data for {cdate}."
            # API returns a list of {timestamp, value, status, ...} dicts
            values = [
                r.get("value") for r in (data if isinstance(data, list) else [])
                if r.get("value") is not None
            ]
            if not values:
                return f"No Body Battery readings found for {cdate}."
            return (
                f"Body Battery ({cdate}):\n"
                f"  Current: {values[-1]}\n"
                f"  High: {max(values)}  Low: {min(values)}"
            )
        except Exception as e:
            return f"Error fetching Body Battery: {e}"

    async def get_stress_data(
        self,
        stress_date: str = None,
        tool_context: ToolContext = None,
    ) -> str:
        """Get stress level summary for a given date (YYYY-MM-DD). Defaults to today."""
        _, err = require_user_id(tool_context, "garmin")
        if err:
            return err

        cdate = stress_date or date.today().isoformat()
        try:
            data = await self._call("get_stress_data", cdate)
            if not data:
                return f"No stress data for {cdate}."
            avg = data.get("avgStressLevel", "N/A")
            max_stress = data.get("maxStressLevel", "N/A")
            return (
                f"Stress ({cdate}):\n"
                f"  Average: {avg}  Max: {max_stress}\n"
                f"  (0–25 low, 26–50 medium, 51–75 high, 76+ very high)"
            )
        except Exception as e:
            return f"Error fetching stress data: {e}"

    async def get_activities(
        self,
        limit: int = 10,
        tool_context: ToolContext = None,
    ) -> str:
        """Get recent Garmin activities (runs, rides, strength sessions, etc.)."""
        _, err = require_user_id(tool_context, "garmin")
        if err:
            return err

        try:
            activities = await self._call("get_activities", 0, min(limit, 50))
            if not activities:
                return "No recent activities found."
            lines = []
            for act in activities:
                name = act.get("activityName", "Activity")
                act_type = act.get("activityType", {}).get("typeKey", "unknown")
                start = (act.get("startTimeLocal") or "")[:16]
                dur_secs = act.get("duration")
                dur = (
                    f"{int(dur_secs) // 3600}h {(int(dur_secs) % 3600) // 60}m"
                    if dur_secs else "N/A"
                )
                dist_m = act.get("distance")
                dist = f", {dist_m / 1000:.2f} km" if dist_m else ""
                act_id = act.get("activityId", "")
                lines.append(
                    f"- [{start}] {name} ({act_type}) — {dur}{dist}"
                    + (f" [ID: {act_id}]" if act_id else "")
                )
            return f"Recent {len(activities)} activities:\n" + "\n".join(lines)
        except Exception as e:
            return f"Error fetching activities: {e}"

    async def get_activity_detail(
        self,
        activity_id: str,
        tool_context: ToolContext = None,
    ) -> str:
        """Get detailed metrics for a specific activity by its ID."""
        _, err = require_user_id(tool_context, "garmin")
        if err:
            return err

        try:
            data = await self._call("get_activity", activity_id)
            if not data:
                return f"No activity found with ID {activity_id}."
            name = data.get("activityName", "Activity")
            act_type = data.get("activityType", {}).get("typeKey", "unknown")
            start = (data.get("startTimeLocal") or "")[:16]
            dur_secs = data.get("duration")
            dur = (
                f"{int(dur_secs) // 3600}h {(int(dur_secs) % 3600) // 60}m"
                if dur_secs else "N/A"
            )
            dist_m = data.get("distance")
            dist = f"{dist_m / 1000:.2f} km" if dist_m else "N/A"
            avg_hr = data.get("averageHR", "N/A")
            max_hr = data.get("maxHR", "N/A")
            calories = data.get("calories", "N/A")
            avg_speed = data.get("averageSpeed")
            pace_str = (
                f"\n  Avg pace: {1000 / avg_speed / 60:.2f} min/km"
                if avg_speed and avg_speed > 0 else ""
            )
            return (
                f"Activity: {name} ({act_type})\n"
                f"  Date: {start}\n"
                f"  Duration: {dur}\n"
                f"  Distance: {dist}\n"
                f"  HR: avg {avg_hr} / max {max_hr} bpm\n"
                f"  Calories: {calories} kcal"
                f"{pace_str}"
            )
        except Exception as e:
            return f"Error fetching activity detail: {e}"

    async def get_heart_rate(
        self,
        hr_date: str = None,
        tool_context: ToolContext = None,
    ) -> str:
        """Get heart rate summary for a given date (YYYY-MM-DD). Defaults to today."""
        _, err = require_user_id(tool_context, "garmin")
        if err:
            return err

        cdate = hr_date or date.today().isoformat()
        try:
            data = await self._call("get_heart_rates", cdate)
            if not data:
                return f"No heart rate data for {cdate}."
            resting = data.get("restingHeartRate", "N/A")
            max_hr = data.get("maxHeartRate", "N/A")
            min_hr = data.get("minHeartRate", "N/A")
            last_hr = data.get("lastHeartRate", "N/A")
            return (
                f"Heart rate ({cdate}):\n"
                f"  Resting: {resting} bpm\n"
                f"  Last reading: {last_hr} bpm\n"
                f"  Day range: {min_hr}–{max_hr} bpm"
            )
        except Exception as e:
            return f"Error fetching heart rate: {e}"

    async def get_training_status(self, tool_context: ToolContext = None) -> str:
        """Get current training status: VO2 max, training load, readiness."""
        _, err = require_user_id(tool_context, "garmin")
        if err:
            return err

        today = date.today().isoformat()
        try:
            data = await self._call("get_training_status", today)
            if not data:
                return "No training status available."
            status = data.get("mostRecentTrainingStatus", {})
            status_key = status.get("trainingStatus", "N/A")
            vo2max = status.get("latestVO2Max")
            load = status.get("trainingLoad")
            result = f"Training status ({today}):\n  Status: {status_key}"
            if vo2max:
                result += f"\n  VO2 max: {vo2max}"
            if load:
                result += f"\n  Training load: {load}"
            return result
        except Exception as e:
            return f"Error fetching training status: {e}"
