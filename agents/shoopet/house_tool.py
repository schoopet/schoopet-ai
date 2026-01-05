"""Google Home (SDM) tool for agent using OAuth tokens."""
import os
from typing import Optional, Dict, Any
from google.adk.tools import ToolContext
from .oauth_client import OAuthClient

# Google SDM API endpoints
SDM_API_BASE = "https://smartdevicemanagement.googleapis.com/v1"


class HouseTool:
    """Tool for accessing Google Home devices via user OAuth tokens.

    This tool fetches OAuth tokens stored by the SMS Gateway and uses them
    to access the user's Google Home devices via SDM API.
    """

    def __init__(self):
        """Initialize the House Tool."""
        self._oauth_client = OAuthClient()
        self._sdm_project_id = os.getenv("GOOGLE_SDM_PROJECT_ID")

    def list_devices(self, tool_context: ToolContext = None) -> str:
        """
        List all connected smart home devices (thermostats, cameras, etc.).
        
        Returns:
            List of devices with their current status and traits.
        """
        if not tool_context or not hasattr(tool_context, 'user_id') or not tool_context.user_id:
            return "ERROR: Cannot access house - no user_id in tool_context."

        phone_number = tool_context.user_id
        
        # Check for valid token using OAuthClient with 'house' feature
        access_token = self._oauth_client.get_valid_access_token(phone_number, "house")
        
        if not access_token:
            oauth_link = self._oauth_client.get_oauth_link(phone_number, "house")
            return (
                f"Your Google Home is not connected yet. "
                f"Please authorize access by visiting:\n{oauth_link}\n\n"
                f"After authorizing, try again."
            )

        if not self._sdm_project_id:
             return "Configuration Error: GOOGLE_SDM_PROJECT_ID not set on server."

        try:
            import httpx
            with httpx.Client() as client:
                url = f"{SDM_API_BASE}/enterprises/{self._sdm_project_id}/devices"
                response = client.get(
                    url,
                    headers={"Authorization": f"Bearer {access_token}"}
                )
                
                if response.status_code == 401:
                    oauth_link = self._oauth_client.get_oauth_link(phone_number, "house")
                    return f"Authorization expired. Please re-authorize: {oauth_link}"
                    
                response.raise_for_status()
                data = response.json()
                
            devices = data.get("devices", [])
            if not devices:
                return "No devices found."
                
            result = []
            for dev in devices:
                # device name format: enterprises/xyz/devices/abc
                dev_id = dev.get("name", "").split("/")[-1]
                dev_type = dev.get("type", "unknown")
                traits = dev.get("traits", {})
                
                # Extract some common info
                info = dev.get("traits", {}).get("sdm.devices.traits.Info", {})
                custom_name = info.get("customName", dev_type)
                
                status_lines = [f"- {custom_name} ({dev_type})"]
                
                # Check for thermostat traits
                if "sdm.devices.traits.Temperature" in traits:
                    temp = traits["sdm.devices.traits.Temperature"].get("ambientTemperatureCelsius")
                    status_lines.append(f"  Temp: {temp}C")
                    
                if "sdm.devices.traits.ThermostatHvac" in traits:
                    status = traits["sdm.devices.traits.ThermostatHvac"].get("status")
                    status_lines.append(f"  HVAC Status: {status}")

                result.append("\n".join(status_lines))
                
            return "\n".join(result)

        except Exception as e:
            return f"Error listing devices: {str(e)}"

    def get_device_status(self, device_name: str, tool_context: ToolContext = None) -> str:
        """
        Get status of a specific device.
        """
        # (Implementation would be similar to list_devices but filtering for specific name)
        # For brevity, reusing list_devices for now or just searching within it
        all_devices = self.list_devices(tool_context)
        # Simple search
        if "http" in all_devices: return all_devices # Return auth link if present
        
        return f"Device status for '{device_name}':\n" + all_devices
