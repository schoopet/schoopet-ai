"""Unit tests for HouseTool."""
import pytest
from unittest.mock import MagicMock, patch, ANY
from agents.shoopet.house_tool import HouseTool
from google.adk.tools import ToolContext

# Sample data
PHONE_NUMBER = "+14155551234"
ACCESS_TOKEN = "access-token-123"
SDM_PROJECT_ID = "sdm-project-123"
OAUTH_LINK = "https://example.com/oauth/google/initiate?token=abc&feature=house"

@pytest.fixture
def tool_context():
    """Create a mock ToolContext."""
    context = MagicMock(spec=ToolContext)
    context.user_id = PHONE_NUMBER
    return context

@pytest.fixture
def house_tool():
    """Create a HouseTool instance with mocked OAuthClient."""
    with patch("agents.shoopet.house_tool.OAuthClient") as mock_client_cls, \
         patch("os.getenv", return_value=SDM_PROJECT_ID):
        
        tool = HouseTool()
        tool._oauth_client = mock_client_cls.return_value
        tool._sdm_project_id = SDM_PROJECT_ID
        
        yield tool

class TestHouseTool:
    """Tests for HouseTool class."""

    def test_initialization(self, house_tool):
        """Should initialize with correct configuration."""
        assert house_tool._sdm_project_id == SDM_PROJECT_ID
        assert house_tool._oauth_client is not None

    @patch("httpx.Client")
    def test_list_devices_success(self, mock_httpx, house_tool, tool_context):
        """Should list devices successfully when authorized."""
        # Mock valid token
        house_tool._oauth_client.get_valid_access_token.return_value = ACCESS_TOKEN
        
        # Mock SDM API response
        mock_client = mock_httpx.return_value.__enter__.return_value
        mock_client.get.return_value.status_code = 200
        mock_client.get.return_value.json.return_value = {
            "devices": [
                {
                    "name": f"enterprises/{SDM_PROJECT_ID}/devices/dev1",
                    "type": "sdm.devices.types.THERMOSTAT",
                    "traits": {
                        "sdm.devices.traits.Info": {"customName": "Living Room Thermostat"},
                        "sdm.devices.traits.Temperature": {"ambientTemperatureCelsius": 21.5},
                        "sdm.devices.traits.ThermostatHvac": {"status": "HEATING"}
                    }
                }
            ]
        }
        
        result = house_tool.list_devices(tool_context)
        
        assert "Living Room Thermostat" in result
        assert "21.5C" in result
        assert "HEATING" in result
        
        # Verify calls
        house_tool._oauth_client.get_valid_access_token.assert_called_with(PHONE_NUMBER, "house")
        
        expected_url = f"https://smartdevicemanagement.googleapis.com/v1/enterprises/{SDM_PROJECT_ID}/devices"
        mock_client.get.assert_called_with(
            expected_url,
            headers={"Authorization": f"Bearer {ACCESS_TOKEN}"}
        )

    @patch("httpx.Client")
    def test_list_devices_no_auth(self, mock_httpx, house_tool, tool_context):
        """Should return auth link if not authorized."""
        # Mock no valid token
        house_tool._oauth_client.get_valid_access_token.return_value = None
        house_tool._oauth_client.get_oauth_link.return_value = OAUTH_LINK
        
        result = house_tool.list_devices(tool_context)
        
        assert "Your Google Home is not connected yet" in result
        assert OAUTH_LINK in result
        
        house_tool._oauth_client.get_oauth_link.assert_called_with(PHONE_NUMBER, "house")

    @patch("httpx.Client")
    def test_list_devices_expired_auth(self, mock_httpx, house_tool, tool_context):
        """Should return auth link if API returns 401."""
        # Mock valid local token but rejected by API
        house_tool._oauth_client.get_valid_access_token.return_value = ACCESS_TOKEN
        house_tool._oauth_client.get_oauth_link.return_value = OAUTH_LINK
        
        mock_client = mock_httpx.return_value.__enter__.return_value
        mock_client.get.return_value.status_code = 401
        
        result = house_tool.list_devices(tool_context)
        
        assert "Authorization expired" in result
        assert OAUTH_LINK in result

    def test_list_devices_no_context(self, house_tool):
        """Should error if tool context missing."""
        result = house_tool.list_devices(None)
        assert "ERROR: Cannot access house" in result
