#!/usr/bin/env bash
# CheckMeIn Kiosk — start script for Raspberry Pi
# Run this from the checkmein-client directory.
set -e

# Ensure we're in the script's directory
cd "$(dirname "$0")"

# Start the client backend
echo "Starting kiosk client..."
python3 client.py &
CLIENT_PID=$!

# Wait for the server to come up
sleep 2

# Open Chromium in kiosk mode
PORT=$(python3 -c "import json; print(json.load(open('config.json')).get('listen_port', 8080))")
echo "Opening kiosk browser on port $PORT"

# Newer Pi OS uses 'chromium', older uses 'chromium-browser'
if command -v chromium-browser &>/dev/null; then
  CHROME=chromium-browser
elif command -v chromium &>/dev/null; then
  CHROME=chromium
else
  echo "ERROR: No Chromium browser found" >&2
  exit 1
fi

$CHROME \
  --kiosk \
  --noerrdialogs \
  --disable-infobars \
  --no-first-run \
  --disable-session-crashed-bubble \
  "http://localhost:${PORT}" &

# If the browser dies, kill the client too
wait $CLIENT_PID
