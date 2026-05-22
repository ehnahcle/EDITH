#!/bin/bash
# EDITH dashboard launcher
# - Runs on port 8511 (JARVIS uses 8501 — no conflict).
# - If already running on 8511, just reopens the browser.
# - Logs to /tmp/edith_dashboard.log

set -e
cd "$(dirname "$0")"

PORT=8511
URL="http://localhost:${PORT}"
LOG=/tmp/edith_dashboard.log

if lsof -nP -iTCP:${PORT} -sTCP:LISTEN >/dev/null 2>&1; then
    echo "✓ EDITH is already running at ${URL}"
else
    echo "→ Starting EDITH dashboard on ${URL} ..."
    nohup ./venv/bin/streamlit run dashboard.py \
        --server.headless true \
        --server.port ${PORT} \
        >"${LOG}" 2>&1 &
    sleep 3
    if lsof -nP -iTCP:${PORT} -sTCP:LISTEN >/dev/null 2>&1; then
        echo "✓ EDITH started (PID $!).  Logs: ${LOG}"
    else
        echo "✗ Failed to start EDITH.  Check ${LOG}"
        exit 1
    fi
fi

# Open in browser (macOS)
open "${URL}" 2>/dev/null || true
