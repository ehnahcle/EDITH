#!/bin/bash
# Stop EDITH dashboard (port 8511 only — does NOT touch JARVIS on 8501).
set -e

PORT=8511
PIDS=$(lsof -nP -iTCP:${PORT} -sTCP:LISTEN -t 2>/dev/null || true)

if [ -z "${PIDS}" ]; then
    echo "EDITH is not running on port ${PORT}."
    exit 0
fi

echo "Stopping EDITH (PIDs: ${PIDS}) ..."
kill ${PIDS}
sleep 1
echo "✓ Stopped."
