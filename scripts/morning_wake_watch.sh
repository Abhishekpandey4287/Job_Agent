#!/bin/bash
# Stays running in the login session.
# A closed lid freezes this process. Opening the lid makes the clock jump,
# and today's job starts if 07:45 has already passed.

set -u
dir="$(cd "$(dirname "$0")" && pwd)"

# Login after 07:45, with no run yet today.
"$dir/morning_if_due.sh" || true

last=$(date +%s)
while true; do
  /bin/sleep 5
  now=$(date +%s)
  gap=$((now - last))
  last=$now
  # A normal wait is about 5 seconds. 15 means the Mac was asleep.
  if (( gap >= 15 )); then
    "$dir/morning_if_due.sh" || true
  fi
done
