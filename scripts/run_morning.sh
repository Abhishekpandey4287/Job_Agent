#!/bin/bash
# Same two steps you run by hand, without the questions.
#
#   Terminal 1: ./scripts/start_brave_debug.sh
#   Terminal 2: .venv/bin/python3 scripts/run_full_search.py
#               yes  → resume agent
#               no   → referral agent
#
# Schedule: 07:45 every day, while you are logged in.
# Log: logs/morning-YYYY-MM-DD.log

set -u

if [[ -z "${MORNING_CAFFEINATED:-}" ]]; then
  export MORNING_CAFFEINATED=1
  exec /usr/bin/caffeinate -i /bin/bash "$0" "$@"
fi

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# One automatic run per day. The wake watcher checks this file.
mark_dir="${HOME}/Library/Application Support/job-search"
mkdir -p "$mark_dir"
mark="${mark_dir}/started-$(date +%Y-%m-%d)"
[[ -f "$mark" ]] || echo "script $(date '+%Y-%m-%d %H:%M:%S')" > "$mark"

PYTHON="$ROOT/.venv/bin/python3"
LOG_DIR="$ROOT/logs"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/morning-$(date +%Y-%m-%d).log"

# pdfLaTeX for locked Backend resume template (TinyTeX or MacTeX)
export PATH="${HOME}/Library/TinyTeX/bin/universal-darwin:/Library/TeX/texbin:${PATH}"


# Show the run in the Terminal window and keep a copy in the log.
exec > >(tee -a "$LOG") 2>&1
echo ""
echo "===== $(date '+%Y-%m-%d %H:%M:%S') morning run ====="

echo "Terminal 1: ./scripts/start_brave_debug.sh"
"$ROOT/scripts/start_brave_debug.sh" --detach

if [[ ! -x "$PYTHON" ]]; then
  echo "Missing $PYTHON"
  exit 1
fi

echo "Terminal 2: .venv/bin/python3 scripts/run_full_search.py"
echo "Resume agent: yes. Referral agent: no."
"$PYTHON" "$ROOT/scripts/run_full_search.py" --with-resume --skip-referral
status=$?
echo "===== $(date '+%Y-%m-%d %H:%M:%S') finished (exit ${status}) ====="
exit "$status"
