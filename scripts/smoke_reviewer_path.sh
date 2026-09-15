#!/usr/bin/env bash
# Smoke-test the path a reviewer actually takes, rather than the units underneath it.
#
# Everything in `make check` tests the system from the inside. None of it runs `./start.sh`, which
# is the command the README and the documentation site put in front of a first-time reader -- and
# that is exactly how `./start.sh ui` came to serve a console with an empty intervention queue
# while its own help text promised the human-in-the-loop flow. Every piece it orchestrated was
# green; the orchestration was wrong, and nothing was looking at it.
#
# So this asserts the two things a reviewer would notice within a minute:
#
#   1. `./start.sh ui` hands them a console with a real escalation waiting.
#   2. Ctrl+C leaves nothing running.
#
# Run: make smoke    (or: bash scripts/smoke_reviewer_path.sh)

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

# The defaults on purpose: they are what a reviewer runs, and they are the ports
# config/policy.yaml allowlists. Anything else is refused by policy, correctly.
APP_PORT="${MOCK_APP_PORT:-8811}"
CONSOLE_PORT="${CONSOLE_PORT:-8812}"
# An explicit template: `mktemp -t NAME` is fine on BSD/macOS and rejected by GNU mktemp,
# which wants the X's. CI is Linux; this was written on a Mac.
LOG="$(mktemp "${TMPDIR:-/tmp}/cua-smoke.XXXXXX")"
START_PID=""

for port in "$APP_PORT" "$CONSOLE_PORT"; do
  if curl --silent --fail --output /dev/null "http://127.0.0.1:${port}/" 2>/dev/null; then
    echo "  something is already listening on :${port}; stop it and re-run" >&2
    exit 1
  fi
done

fail() {
  echo "  FAIL: $*" >&2
  echo "  --- start.sh output ---" >&2
  tail -25 "$LOG" >&2 || true
  exit 1
}

cleanup() {
  [[ -n "$START_PID" ]] && kill -TERM "$START_PID" 2>/dev/null || true
  # Give the script's own trap a moment to take its children down before checking for orphans.
  sleep 3
  pkill -f "mock_bank.server --port $APP_PORT" 2>/dev/null || true
  pkill -f "console --port $CONSOLE_PORT" 2>/dev/null || true
  rm -f "$LOG"
}
trap cleanup EXIT

echo "==> ./start.sh ui  (app :$APP_PORT, console :$CONSOLE_PORT)"
MOCK_APP_PORT="$APP_PORT" CONSOLE_PORT="$CONSOLE_PORT" ./start.sh ui >"$LOG" 2>&1 &
START_PID=$!

for _ in $(seq 1 120); do
  curl --silent --fail --output /dev/null "http://127.0.0.1:${CONSOLE_PORT}/" && break
  sleep 1
  kill -0 "$START_PID" 2>/dev/null || fail "start.sh exited before the console came up"
done
curl --silent --fail --output /dev/null "http://127.0.0.1:${CONSOLE_PORT}/" \
  || fail "console never answered on :${CONSOLE_PORT}"

# The point of the mode. An empty queue here means a reader is shown a console that cannot
# demonstrate the one flow it exists for -- which is how this broke the first time.
pending=$(curl --silent "http://127.0.0.1:${CONSOLE_PORT}/api/interventions" \
  | python3 -c 'import json,sys; print(len(json.load(sys.stdin)))')
[[ "$pending" -ge 1 ]] || fail "console served an empty intervention queue (got $pending)"
echo "  ok: $pending intervention(s) waiting"

state=$(curl --silent "http://127.0.0.1:${CONSOLE_PORT}/api/state" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["state"])')
[[ "$state" == "paused" ]] || fail "expected a paused session awaiting an operator, got '$state'"
echo "  ok: session is $state, awaiting an operator"

echo "==> shutting down"
kill -TERM "$START_PID"
START_PID=""
sleep 4

# `|| true` is load-bearing: pgrep exits 1 when it matches nothing, and under `set -o pipefail`
# that makes the whole assignment fail -- so without it this check killed the script precisely
# when there were no orphans, which is the case it is supposed to pass on.
orphans=$(pgrep -f "mock_bank.server --port $APP_PORT|console --port $CONSOLE_PORT" || true)
count=$(printf '%s' "$orphans" | grep -c . || true)
[[ "$count" -eq 0 ]] || fail "$count process(es) survived the shutdown: $orphans"
echo "  ok: nothing left running"

echo "PASS: the reviewer path works end to end"
