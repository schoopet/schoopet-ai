#!/bin/bash
# Convenience wrapper for Schoopet chat client

cd "$(dirname "$0")/agents"
source ../schoopet/.venv/bin/activate 2>/dev/null || source ../.venv/bin/activate
python -m schoopet.chat "$@"
