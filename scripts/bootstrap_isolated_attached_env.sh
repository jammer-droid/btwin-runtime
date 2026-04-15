#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"

COMMAND="${1:-start}"
if [[ $# -gt 0 ]]; then
  shift
fi

ROOT_DIR="${BTWIN_BOOTSTRAP_ROOT:-${REPO_ROOT}/.btwin-attached-test}"
PROJECT_ROOT="${BTWIN_BOOTSTRAP_PROJECT_ROOT:-${REPO_ROOT}}"
PROJECT_NAME="${BTWIN_BOOTSTRAP_PROJECT:-btwin-runtime-attached-test}"
PORT="${BTWIN_BOOTSTRAP_PORT:-8788}"
SKIP_SERVER=0
BTWIN_BIN="${BTWIN_BIN:-btwin}"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --root)
      ROOT_DIR="$2"
      shift 2
      ;;
    --project-root)
      PROJECT_ROOT="$2"
      shift 2
      ;;
    --project)
      PROJECT_NAME="$2"
      shift 2
      ;;
    --port)
      PORT="$2"
      shift 2
      ;;
    --skip-server)
      SKIP_SERVER=1
      shift
      ;;
    *)
      echo "Unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

ROOT_DIR="$(cd "$(dirname "$ROOT_DIR")" && pwd)/$(basename "$ROOT_DIR")"
PROJECT_ROOT="$(cd "$PROJECT_ROOT" && pwd)"
CONFIG_PATH="${ROOT_DIR}/config.yaml"
DATA_DIR="${ROOT_DIR}/data"
LOG_DIR="${ROOT_DIR}/logs"
PID_PATH="${ROOT_DIR}/serve-api.pid"
ENV_PATH="${ROOT_DIR}/env.sh"
API_URL="http://127.0.0.1:${PORT}"

require_btwin() {
  if ! command -v "$BTWIN_BIN" >/dev/null 2>&1; then
    echo "Could not find \`${BTWIN_BIN}\` in PATH." >&2
    exit 1
  fi
}

write_env_file() {
  mkdir -p "$ROOT_DIR"
  cat > "$ENV_PATH" <<EOF
export BTWIN_CONFIG_PATH="${CONFIG_PATH}"
export BTWIN_DATA_DIR="${DATA_DIR}"
export BTWIN_API_URL="${API_URL}"
export BTWIN_TEST_ROOT="${ROOT_DIR}"
export BTWIN_TEST_PID_PATH="${PID_PATH}"
export BTWIN_TEST_LOG_DIR="${LOG_DIR}"
if [[ -d "${REPO_ROOT}/.venv/bin" ]]; then
  export PATH="${REPO_ROOT}/.venv/bin:\$PATH"
fi

btwin_test_status() {
  echo "Root: \${BTWIN_TEST_ROOT}"
  echo "API: \${BTWIN_API_URL}"
  echo "PID file: \${BTWIN_TEST_PID_PATH}"
  if [[ -f "\${BTWIN_TEST_PID_PATH}" ]]; then
    echo "PID: \$(cat "\${BTWIN_TEST_PID_PATH}")"
  else
    echo "PID: missing"
  fi
  if curl -fsS "\${BTWIN_API_URL}/api/sessions/status" >/dev/null 2>&1; then
    echo "API health: ok"
  else
    echo "API health: unavailable"
  fi
}

btwin_test_up() {
  if curl -fsS "\${BTWIN_API_URL}/api/sessions/status" >/dev/null 2>&1; then
    echo "Isolated attached API already running."
    return 0
  fi
  mkdir -p "\${BTWIN_TEST_LOG_DIR}"
  if [[ -f "\${BTWIN_TEST_PID_PATH}" ]]; then
    local pid
    pid="\$(cat "\${BTWIN_TEST_PID_PATH}")"
    if [[ -n "\${pid}" ]] && kill -0 "\${pid}" >/dev/null 2>&1; then
      echo "Isolated attached API already running."
      return 0
    fi
  fi
  nohup btwin serve-api --port "\${BTWIN_API_URL##*:}" \\
    </dev/null \\
    >"\${BTWIN_TEST_LOG_DIR}/serve-api.stdout.log" \\
    2>"\${BTWIN_TEST_LOG_DIR}/serve-api.stderr.log" &
  printf '%s\\n' "\$!" > "\${BTWIN_TEST_PID_PATH}"
  local attempt
  for attempt in \$(seq 1 40); do
    if curl -fsS "\${BTWIN_API_URL}/api/sessions/status" >/dev/null 2>&1; then
      echo "Isolated attached API ready."
      return 0
    fi
    sleep 0.25
  done
  echo "Timed out waiting for serve-api at \${BTWIN_API_URL}" >&2
  return 1
}

btwin_test_hud() {
  if ! curl -fsS "\${BTWIN_API_URL}/api/sessions/status" >/dev/null 2>&1; then
    echo "Isolated attached API is not running. Run btwin_test_up first." >&2
    return 1
  fi
  btwin hud "\$@"
}

btwin_test_down() {
  if [[ -f "\${BTWIN_TEST_PID_PATH}" ]]; then
    local pid
    pid="\$(cat "\${BTWIN_TEST_PID_PATH}")"
    if [[ -n "\${pid}" ]] && kill -0 "\${pid}" >/dev/null 2>&1; then
      kill "\${pid}" >/dev/null 2>&1 || true
    fi
    rm -f "\${BTWIN_TEST_PID_PATH}"
  fi
  echo "Stopped isolated attached API (if running)."
}
EOF
}

run_btwin() {
  BTWIN_CONFIG_PATH="$CONFIG_PATH" \
  BTWIN_DATA_DIR="$DATA_DIR" \
  BTWIN_API_URL="$API_URL" \
  "$BTWIN_BIN" "$@"
}

wait_for_api() {
  local attempt
  for attempt in $(seq 1 40); do
    if curl -fsS "${API_URL}/api/sessions/status" >/dev/null 2>&1; then
      return 0
    fi
    sleep 0.25
  done
  echo "Timed out waiting for serve-api at ${API_URL}" >&2
  return 1
}

stop_server() {
  if [[ -f "$PID_PATH" ]]; then
    local pid
    pid="$(cat "$PID_PATH")"
    if [[ -n "$pid" ]] && kill -0 "$pid" >/dev/null 2>&1; then
      kill "$pid" >/dev/null 2>&1 || true
      wait "$pid" 2>/dev/null || true
    fi
    rm -f "$PID_PATH"
  fi
}

start_env() {
  require_btwin
  mkdir -p "$DATA_DIR" "$LOG_DIR" "$PROJECT_ROOT"
  write_env_file

  run_btwin setup >/dev/null
  (
    cd "$PROJECT_ROOT"
    run_btwin init "$PROJECT_NAME" --local --force >/dev/null
  )

  if [[ "$SKIP_SERVER" == "1" ]]; then
    echo "Isolated attached env prepared (server skipped)."
    echo "Root: $ROOT_DIR"
    echo "Project root: $PROJECT_ROOT"
    echo "Env file: $ENV_PATH"
    return 0
  fi

  stop_server

  nohup env \
    BTWIN_CONFIG_PATH="$CONFIG_PATH" \
    BTWIN_DATA_DIR="$DATA_DIR" \
    BTWIN_API_URL="$API_URL" \
    "$BTWIN_BIN" serve-api --port "$PORT" \
    </dev/null \
    >"$LOG_DIR/serve-api.stdout.log" \
    2>"$LOG_DIR/serve-api.stderr.log" &
  echo $! > "$PID_PATH"

  wait_for_api

  echo "Isolated attached env ready."
  echo "Root: $ROOT_DIR"
  echo "Project root: $PROJECT_ROOT"
  echo "API: $API_URL"
  echo "Env file: $ENV_PATH"
  echo "PID: $(cat "$PID_PATH")"
  echo "Next: source \"$ENV_PATH\" && cd \"$PROJECT_ROOT\" && codex"
}

print_env() {
  write_env_file
  cat "$ENV_PATH"
}

show_status() {
  echo "Root: $ROOT_DIR"
  echo "Project root: $PROJECT_ROOT"
  echo "Config: $CONFIG_PATH"
  echo "Data dir: $DATA_DIR"
  echo "API: $API_URL"
  if [[ -f "$PID_PATH" ]]; then
    echo "PID file: $PID_PATH ($(cat "$PID_PATH"))"
  else
    echo "PID file: missing"
  fi
  if curl -fsS "${API_URL}/api/sessions/status" >/dev/null 2>&1; then
    echo "API health: ok"
  else
    echo "API health: unavailable"
  fi
}

case "$COMMAND" in
  start)
    start_env
    ;;
  env)
    print_env
    ;;
  status)
    show_status
    ;;
  stop)
    stop_server
    echo "Stopped isolated serve-api (if running)."
    ;;
  *)
    echo "Usage: $0 {start|env|status|stop} [--root PATH] [--project-root PATH] [--project NAME] [--port PORT] [--skip-server]" >&2
    exit 2
    ;;
esac
