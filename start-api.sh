#!/bin/bash
# Start OpenFinops API server
# Run as: bash start-api.sh
cd "$(dirname "$0")/apps/api"
exec python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
