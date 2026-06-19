#!/usr/bin/env bash
# Launcher Chrome executes for the FlowMind native messaging host.
# Requires python3 with the `websockets` package available on PATH.
exec python3 "$(dirname "$0")/flowmind_host.py"
