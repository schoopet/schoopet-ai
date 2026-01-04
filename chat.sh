#!/bin/bash
# Convenience wrapper for Shoopet chat client

cd "$(dirname "$0")/agents"
source ../shoopet/.venv/bin/activate 2>/dev/null || source ../.venv/bin/activate
python -m shoopet.chat "$@"
