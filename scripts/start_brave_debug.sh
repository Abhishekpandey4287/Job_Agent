#!/bin/bash
# Launch Brave with the Switch profile and remote debugging enabled.
#
# Usage: ./scripts/start_brave_debug.sh
#
# IMPORTANT: Fully quit Brave first (Cmd+Q), otherwise you will see
# "Opening in existing browser session" and CDP will NOT work.

BRAVE="/Applications/Brave Browser.app/Contents/MacOS/Brave Browser"
PROFILE="Profile 2"
PORT=9222

if curl -s --max-time 2 "http://127.0.0.1:${PORT}/json/version" >/dev/null 2>&1; then
  echo "✓ Brave CDP already active on port ${PORT}"
  curl -s "http://127.0.0.1:${PORT}/json/version" | head -1
  echo ""
  echo "You can run the job search in another terminal:"
  echo "  cd $(cd "$(dirname "$0")/.." && pwd)"
  echo "  .venv/bin/python3 scripts/run_full_search.py"
  exit 0
fi

if pgrep -x "Brave Browser" >/dev/null 2>&1 || pgrep -f "Brave Browser.app" >/dev/null 2>&1; then
  echo "⚠️  Brave is already running."
  echo "   CDP will NOT work until you fully quit Brave (Cmd+Q — all windows)."
  echo ""
  read -r -p "Quit Brave now and relaunch with debugging? [y/N] " answer
  if [[ "$answer" =~ ^[Yy]$ ]]; then
    osascript -e 'tell application "Brave Browser" to quit' 2>/dev/null || true
    sleep 3
  else
    echo "Aborted. Quit Brave manually, then run this script again."
    exit 1
  fi
fi

echo "Launching Brave (Switch profile) with remote debugging on port ${PORT}..."
echo "Keep this terminal open. The job agent will connect via CDP."
echo ""
echo "Success looks like: DevTools listening on ws://127.0.0.1:${PORT}/..."
echo "Failure looks like: Opening in existing browser session"
echo ""

"$BRAVE" \
  --profile-directory="$PROFILE" \
  --remote-debugging-port="$PORT" \
  --no-first-run \
  --no-default-browser-check \
  about:blank
