#!/usr/bin/env bash
# SENTINEL AI — Single-command start
# Usage: ./start.sh
set -e

echo ""
echo "╔══════════════════════════════════════════════════════════╗"
echo "║         SENTINEL AI — Industrial Safety Platform         ║"
echo "║         Multi-Agent Compound Risk Intelligence           ║"
echo "╚══════════════════════════════════════════════════════════╝"
echo ""

# Check Python
python3 --version >/dev/null 2>&1 || { echo "ERROR: Python 3.12+ required"; exit 1; }

# Install dependencies if needed
if ! python3 -c "import fastapi, sklearn, networkx" 2>/dev/null; then
  echo "Installing dependencies..."
  pip install -r requirements.txt --break-system-packages -q
fi

echo "Starting SENTINEL AI on http://localhost:8000"
echo ""
echo "  Dashboard:   http://localhost:8000"
echo "  API docs:    http://localhost:8000/docs"
echo "  Health:      http://localhost:8000/api/health"
echo ""
echo "  Scenarios:   POST /api/scenarios/{id}/trigger"
echo "  Stress test: POST /api/stress-test/trigger?n_spikes=50"
echo ""
echo "Press Ctrl+C to stop."
echo ""

python3 -m uvicorn backend.main:app --host 0.0.0.0 --port 8000 --reload
