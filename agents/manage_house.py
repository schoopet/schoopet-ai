"""CLI utility to test HouseTool functionality manually."""
import sys
import argparse
import os
from google.adk.tools import ToolContext
from schoopet.house_tool import HouseTool

def main():
    parser = argparse.ArgumentParser(description="Manage House Tool (Smart Home) manually")
    parser.add_argument("action", choices=["list", "status"], help="Action to perform")
    parser.add_argument("--phone", required=True, help="User phone number (E.164 format, e.g., +14155551234)")
    parser.add_argument("--device", help="Device name (required for 'status' action)")
    
    args = parser.parse_args()

    # Ensure environment variables are set (user should set these before running)
    required_vars = ["GOOGLE_CLOUD_PROJECT", "GOOGLE_OAUTH_CLIENT_ID", "GOOGLE_OAUTH_CLIENT_SECRET", "GOOGLE_SDM_PROJECT_ID"]
    missing = [var for var in required_vars if not os.getenv(var)]
    if missing:
        print(f"Error: Missing environment variables: {', '.join(missing)}")
        print("Please export them before running this script.")
        sys.exit(1)

    # Initialize tool
    tool = HouseTool()
    
    # Create context
    context = ToolContext(user_id=args.phone)

    print(f"--- Running HouseTool Action: {args.action} for {args.phone} ---")
    
    if args.action == "list":
        result = tool.list_devices(context)
        print(result)
        
    elif args.action == "status":
        if not args.device:
            print("Error: --device is required for 'status' action")
            sys.exit(1)
        result = tool.get_device_status(args.device, context)
        print(result)

if __name__ == "__main__":
    main()