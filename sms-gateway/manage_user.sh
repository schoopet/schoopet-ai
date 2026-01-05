#!/bin/bash
# Wrapper to run the python management script
set -e

# Determine script directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Check if venv exists and activate it
if [ -d ".venv" ]; then
    source .venv/bin/activate
elif [ -d "sms-gateway/.venv" ]; then
    source sms-gateway/.venv/bin/activate
fi

# Run the python script
# Assuming this script is run from sms-gateway/ root or project root
# If this file is in sms-gateway/, and manage_user.py is in scripts/

python3 scripts/manage_user.py "$@"
