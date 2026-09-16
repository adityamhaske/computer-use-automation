#!/usr/bin/env bash
# start.sh — one entrypoint to run this project end to end, in whichever mode you need.
#
# Usage:
#   ./start.sh [mode] [options]
#   ./start.sh                 # same as: ./start.sh demo
#   ./start.sh ui              # mock back-office + operator console together, for manual poking
#   ./start.sh --help
#
# Every mode below is orchestration over the Makefile targets already used for grading
# (`make setup && make demo`) — this script adds nothing you couldn't run by hand, it just gives
# the combinations (app + console together, wait-for-ready, teardown on Ctrl+C) a name.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PY=".venv/bin/python"
CUA=".venv/bin/cua"
MODE="demo"
SKIP_SETUP=0
HEADLESS="${CUA_HEADLESS:-true}"
BARE_CONSOLE=0

# What `console`/`ui` supervises so the queue is not empty. A capability that fails closed on
# an armed fault is the shortest honest route to a real intervention -- nothing is faked.
DEMO_CAPABILITY="corebank.member.savings_balance@1.0.0"
DEMO_MEMBER="12345"
DEMO_FAULT="undeclared_dialog"
MOCK_APP_PORT="${MOCK_APP_PORT:-8811}"
CONSOLE_PORT="${CONSOLE_PORT:-8812}"

# ------------------------------------------------------------------------- help

usage() {
  cat <<'EOF'
start.sh — run the Computer-Use Automation project end to end.

MODES
  demo        (default) The graded story, one command: setup (if needed) + `make demo`.
                Works with no API key — discovery falls back to a recorded transcript and says so.
  app         Just the hostile mock back-office, in the foreground, on --app-port (default 8811).
  app-b       The same app as the "second tenant" variant (rebranded, restyled), same port.
  console     The operator console with a real escalation already waiting. Starts the mock
                back-office in the background, waits for it to answer, then runs a capability
                against the console's own session with a fault armed — so the intervention queue
                has something in it and the human handoff is reachable from the served UI. Ctrl+C
                stops both. Pass --bare for a console supervising an idle session instead.
  ui          Alias for `console` — the phrase people reach for when they mean "show me the app".
  check       `make check` — lint + strict typecheck + architectural invariants + full test suite.
  test        `make test` — the offline suite only (no API key, no network).
  eval        `make eval` — stability + cross-tenant measurement, written to evidence/evals/.
  setup       `make setup` only — venv, dependencies, Chromium. (Every other mode does this for you
                automatically the first time; use this to do it on its own.)

OPTIONS
  --app-port PORT       Port for the mock back-office (default 8811, or $MOCK_APP_PORT).
  --console-port PORT   Port for the operator console (default 8812, or $CONSOLE_PORT).
  --headed              Run the console's supervised browser headed (visible), not headless.
  --bare                 console/ui: supervise an idle session with no capability and no fault.
                           The queue stays empty and the handoff cannot be demonstrated.
  --skip-setup           Assume .venv already exists; fail instead of auto-running `make setup`.
  -h, --help             Show this message.

EXAMPLES
  ./start.sh                          # the whole graded demo
  ./start.sh ui                       # console with an escalation waiting — claim it, act, hand back
  ./start.sh ui --headed              # ...and watch the real browser window while you do it
  ./start.sh app-b --app-port 9001    # the rebranded tenant, on a different port
  ./start.sh check                    # what CI runs
EOF
}

# ------------------------------------------------------------------------- arg parsing

if [[ $# -gt 0 && "$1" != --* ]]; then
  MODE="$1"
  shift
fi

while [[ $# -gt 0 ]]; do
  case "$1" in
    --app-port) MOCK_APP_PORT="$2"; shift 2 ;;
    --console-port) CONSOLE_PORT="$2"; shift 2 ;;
    --headed) HEADLESS="false"; shift ;;
    --skip-setup) SKIP_SETUP=1; shift ;;
    --bare) BARE_CONSOLE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "start.sh: unrecognized option '$1'" >&2; usage >&2; exit 2 ;;
  esac
done

export MOCK_APP_PORT CONSOLE_PORT CUA_HEADLESS="$HEADLESS"

# ------------------------------------------------------------------------- setup

ensure_setup() {
  if [[ -x "$PY" ]]; then
    return
  fi
  if [[ "$SKIP_SETUP" == "1" ]]; then
    echo "start.sh: no .venv found and --skip-setup was passed. Run \`make setup\` first." >&2
    exit 1
  fi
  echo "==> No .venv found — running \`make setup\` first (venv, deps, Chromium)."
  make setup
}

wait_for() {
  # Poll a URL until it answers or $2 seconds pass. Used so `console`/`ui` mode doesn't hand the
  # console a target that isn't listening yet.
  local url="$1" timeout="${2:-30}" waited=0
  until curl --silent --fail --output /dev/null "$url" 2>/dev/null; do
    sleep 0.5
    waited=$((waited + 1))
    if [[ $((waited / 2)) -ge "$timeout" ]]; then
      echo "start.sh: timed out waiting for $url to come up" >&2
      return 1
    fi
  done
}

# ------------------------------------------------------------------------- modes

run_demo() {
  ensure_setup
  make demo
}

run_app() {
  ensure_setup
  echo "==> Mock back-office on http://127.0.0.1:${MOCK_APP_PORT}"
  exec "$PY" -m apps.mock_bank.server --port "$MOCK_APP_PORT"
}

run_app_variant_b() {
  ensure_setup
  echo "==> Mock back-office (variant_b) on http://127.0.0.1:${MOCK_APP_PORT}"
  exec "$PY" -m apps.mock_bank.server --port "$MOCK_APP_PORT" --variant variant_b
}

run_console() {
  ensure_setup

  # The supervised session drives the app through the policy chokepoint, and config/policy.yaml
  # allowlists the default ports and nothing else. Overriding them used to fail several seconds
  # later with a NavigationBlockedError from inside the executor, which reads like a defect in the
  # system rather than a flag that needs a matching policy entry. Say so here instead.
  if [[ "$MOCK_APP_PORT" != "8811" || "$CONSOLE_PORT" != "8812" ]]; then
    if ! grep -q "$MOCK_APP_PORT" config/policy.yaml; then
      echo "start.sh: the console drives the app through the policy allowlist, and" >&2
      echo "  127.0.0.1:${MOCK_APP_PORT} is not in it. Either use the default ports, or add the" >&2
      echo "  host to 'allowlist' in config/policy.yaml. (\`./start.sh app --app-port\` is fine --" >&2
      echo "  it is only the supervised console that dispatches through policy.)" >&2
      exit 2
    fi
  fi

  echo "==> Starting the mock back-office in the background on :${MOCK_APP_PORT}"
  "$PY" -m apps.mock_bank.server --port "$MOCK_APP_PORT" &
  local app_pid=$!
  local console_pid=""

  # Both children are backgrounded and killed explicitly by pid here, rather than left as a plain
  # foreground command relying on the terminal delivering Ctrl+C to the whole process group — that
  # relies on job-control semantics this script cannot guarantee (e.g. under `nohup`, a supervisor,
  # or a non-interactive shell). `wait "$console_pid"` below is what actually makes the trap fire
  # promptly on a signal; killing both pids by hand is what makes it deterministic once it does.
  local cleaned_up=0
  cleanup() {
    [[ "$cleaned_up" == "1" ]] && return   # the EXIT trap fires again after INT/TERM's own trap
                                            # handler returns -- without this guard, "Shutting
                                            # down" (and the kills) would run twice on every signal.
    cleaned_up=1
    echo
    echo "==> Shutting down"
    [[ -n "$console_pid" ]] && kill "$console_pid" 2>/dev/null || true
    kill "$app_pid" 2>/dev/null || true
    [[ -n "$console_pid" ]] && wait "$console_pid" 2>/dev/null || true
    wait "$app_pid" 2>/dev/null || true
  }
  trap cleanup EXIT INT TERM

  if ! wait_for "http://127.0.0.1:${MOCK_APP_PORT}/" 30; then
    exit 1
  fi

  # Built as an array rather than an unquoted command substitution: the old
  # `$( [[ ... ]] && echo --no-headless )` relied on word-splitting an empty string to mean "no
  # flag", which shellcheck flags (SC2046) and which breaks the moment an argument contains a
  # space. An array says "these are the arguments" without relying on splitting at all.
  local args=(console --port "$CONSOLE_PORT" --target "http://127.0.0.1:${MOCK_APP_PORT}/")
  [[ "$HEADLESS" == "false" ]] && args+=(--no-headless)

  if [[ "$BARE_CONSOLE" == "1" ]]; then
    echo "==> Operator console on http://127.0.0.1:${CONSOLE_PORT}  (--bare: idle session)"
    echo "    the intervention queue will be empty — nothing can escalate on an idle session"
  else
    # Without a capability the console supervises a session that can never escalate, so the
    # queue stays empty and the one flow this console exists for is unreachable from the served
    # UI -- which is exactly what someone typing `./start.sh ui` is trying to look at. So run a
    # real capability against the console's own session with a fault armed, and hand them a
    # console with an escalation already in it. `--bare` restores the idle behaviour.
    args+=(--sign-in
           --capability "$DEMO_CAPABILITY"
           --input "member_id=${DEMO_MEMBER}"
           --arm-fault "$DEMO_FAULT")
    echo "==> Operator console on http://127.0.0.1:${CONSOLE_PORT}"
    echo "    an escalation will be waiting — claim it, act on the page, hand it back"
  fi
  echo "    supervising  : http://127.0.0.1:${MOCK_APP_PORT}/"
  echo "    supervised browser headless: ${HEADLESS}"

  "$CUA" "${args[@]}" &
  console_pid=$!
  wait "$console_pid"
}

run_check() {
  ensure_setup
  make check
}

run_test() {
  ensure_setup
  make test
}

run_eval() {
  ensure_setup
  make eval
}

run_setup() {
  ensure_setup
  echo "==> Setup complete."
}

# ------------------------------------------------------------------------- dispatch

case "$MODE" in
  demo) run_demo ;;
  app) run_app ;;
  app-b|app-variant-b) run_app_variant_b ;;
  console|ui) run_console ;;
  check) run_check ;;
  test) run_test ;;
  eval) run_eval ;;
  setup) run_setup ;;
  -h|--help) usage ;;
  *)
    echo "start.sh: unknown mode '$MODE'" >&2
    usage >&2
    exit 2
    ;;
esac
