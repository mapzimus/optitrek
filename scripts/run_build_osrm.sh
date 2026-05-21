#!/usr/bin/env bash
# Wrapper for build_osrm.sh that sets the right PATH + env before invoking
# the real script. Use this when launching from a non-interactive context
# (e.g. Start-Process from PowerShell, Task Scheduler) where Git Bash's
# login profile would otherwise produce unwanted env-dump output.
set -euo pipefail
export PATH="/c/Program Files/Docker/Docker/resources/bin:$PATH"
export MSYS_NO_PATHCONV=1
export OSRM_THREADS="${OSRM_THREADS:-6}"
cd /e/dev/optitrek
exec scripts/build_osrm.sh
