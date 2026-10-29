#!/bin/bash
# Start today's job once, after 07:45 local time.
# Launchd runs the copy in ~/Library/Application Support/job-search
# because it is not allowed to read ~/Downloads.

set -u

hour=$(date +%H)
minute=$(date +%M)
now=$((10#$hour * 60 + 10#$minute))
due=$((7 * 60 + 45))
if (( now < due )); then
  exit 0
fi

mark_dir="${HOME}/Library/Application Support/job-search"
mkdir -p "$mark_dir"
mark="${mark_dir}/started-$(date +%Y-%m-%d)"
if [[ -f "$mark" ]]; then
  exit 0
fi

if ! (set -o noclobber; echo "$(date '+%Y-%m-%d %H:%M:%S')" > "$mark") 2>/dev/null; then
  exit 0
fi

command_file="/Users/abhishekpandey/Downloads/job/scripts/run_morning.command"
if ! /usr/bin/open -a Terminal "$command_file"; then
  rm -f "$mark"
  exit 1
fi
