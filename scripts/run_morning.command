#!/bin/bash
# Opened by the 7:45 schedule inside Terminal.
# Terminal is allowed to read ~/Downloads; the schedule itself is not.
exec "$(cd "$(dirname "$0")" && pwd)/run_morning.sh"
