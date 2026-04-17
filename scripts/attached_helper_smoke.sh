#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
BOOTSTRAP_SCRIPT="${SCRIPT_DIR}/bootstrap_isolated_attached_env.sh"

ROOT_DIR="${BTWIN_ATTACHED_SMOKE_ROOT:-$(mktemp -d "${TMPDIR:-/tmp}/btwin-attached-smoke.XXXXXX")}"
PROJECT_ROOT="${ROOT_DIR}/project"
HOME_DIR="${ROOT_DIR}/home"
PROJECT_NAME="${BTWIN_ATTACHED_SMOKE_PROJECT:-btwin-attached-smoke}"
KEEP_ROOT="${BTWIN_ATTACHED_SMOKE_KEEP_ROOT:-0}"
if [[ -n "${BTWIN_ATTACHED_SMOKE_ROOT:-}" ]]; then
  SMOKE_ROOT_IS_TEMP=0
else
  SMOKE_ROOT_IS_TEMP=1
fi
PORT=""
export BTWIN_ATTACHED_SMOKE_ROOT="${ROOT_DIR}"
mkdir -p "${PROJECT_ROOT}" "${HOME_DIR}"

resolve_btwin_bin() {
  if [[ -x "${REPO_ROOT}/.venv/bin/btwin" ]]; then
    printf '%s\n' "${REPO_ROOT}/.venv/bin/btwin"
    return 0
  fi

  if command -v btwin >/dev/null 2>&1; then
    command -v btwin
    return 0
  fi

  echo "Could not find a usable btwin executable in .venv/bin or PATH." >&2
  exit 1
}

find_free_port() {
  HOME="${HOME_DIR}" uv run --project "${REPO_ROOT}" python - <<'PY'
import socket

sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
sock.bind(("127.0.0.1", 0))
try:
    print(sock.getsockname()[1])
finally:
    sock.close()
PY
}

BTWIN_BIN_PATH="$(resolve_btwin_bin)"

cleanup() {
  if [[ -n "${PORT}" ]]; then
    HOME="${HOME_DIR}" BTWIN_BIN="${BTWIN_BIN_PATH}" "${BOOTSTRAP_SCRIPT}" stop --root "${ROOT_DIR}" --port "${PORT}" >/dev/null 2>&1 || true
  fi
  if [[ "${KEEP_ROOT}" != "1" && "${SMOKE_ROOT_IS_TEMP}" == "1" ]]; then
    rm -rf "${ROOT_DIR}"
  fi
}
trap cleanup EXIT

start_bootstrap() {
  local attempt port
  for attempt in 1 2 3 4 5; do
    port="${BTWIN_ATTACHED_SMOKE_PORT:-$(find_free_port)}"
    PORT="${port}"
    if HOME="${HOME_DIR}" BTWIN_BIN="${BTWIN_BIN_PATH}" "${BOOTSTRAP_SCRIPT}" start --root "${ROOT_DIR}" --project-root "${PROJECT_ROOT}" --project "${PROJECT_NAME}" --port "${PORT}"; then
      return 0
    fi
    HOME="${HOME_DIR}" BTWIN_BIN="${BTWIN_BIN_PATH}" "${BOOTSTRAP_SCRIPT}" stop --root "${ROOT_DIR}" --port "${PORT}" >/dev/null 2>&1 || true
    if [[ -n "${BTWIN_ATTACHED_SMOKE_PORT:-}" ]]; then
      break
    fi
    sleep 0.2
  done
  return 1
}

echo "Bootstrapping isolated attached env..."
if ! start_bootstrap; then
  echo "Failed to bootstrap isolated attached env after retries." >&2
  exit 1
fi

# shellcheck source=/dev/null
source "${ROOT_DIR}/env.sh"

run_btwin() {
  HOME="${HOME_DIR}" BTWIN_CONFIG_PATH="${BTWIN_CONFIG_PATH}" BTWIN_DATA_DIR="${BTWIN_DATA_DIR}" BTWIN_API_URL="${BTWIN_API_URL}" "${BTWIN_BIN_PATH}" "$@"
}

run_python() {
  HOME="${HOME_DIR}" uv run --project "${REPO_ROOT}" python "$@"
}

mkdir -p "${BTWIN_DATA_DIR}/protocols"
cat > "${BTWIN_DATA_DIR}/protocols/attached-helper-smoke.yaml" <<'YAML'
name: attached-helper-smoke
description: Minimal protocol for attached helper smoke
phases:
  - name: context
    actions: [contribute, discuss]
    template: []
  - name: discussion
    actions: [discuss]
YAML

echo "Running attached helper flow..."
run_btwin agent create alice --provider codex --role implementer --model gpt-5 >/dev/null
THREAD_JSON="$(run_btwin thread create --topic "Attached helper smoke" --protocol attached-helper-smoke --participant alice --participant bob --json)"
THREAD_ID="$(JSON_PAYLOAD="${THREAD_JSON}" run_python -c 'import json, os; print(json.loads(os.environ["JSON_PAYLOAD"])["thread_id"])')"
run_btwin thread send-message --thread "${THREAD_ID}" --from bob --content "Attached smoke ping." --tldr "smoke ping" --delivery-mode broadcast --json >/dev/null
RUN_BIND_JSON="$(run_btwin runtime bind --thread "${THREAD_ID}" --agent alice --json)"
CURRENT_JSON="$(run_btwin runtime current --json)"
NEXT_JSON="$(run_btwin protocol next --thread "${THREAD_ID}" --json)"
APPLY_JSON="$(run_btwin protocol apply-next --json)"
MAILBOX_JSON="$(BTWIN_ATTACHED_SMOKE_THREAD_ID="${THREAD_ID}" run_python - <<'PY'
import json
import os
import urllib.request

base = os.environ["BTWIN_API_URL"]
thread_id = os.environ["BTWIN_ATTACHED_SMOKE_THREAD_ID"]
with urllib.request.urlopen(f"{base}/api/system-mailbox?threadId={thread_id}&limit=5") as response:
    print(response.read().decode("utf-8"))
PY
)"
HUD_OUTPUT="$(run_btwin hud --thread "${THREAD_ID}" --limit 5)"
CLEARED_JSON="$(run_btwin runtime clear --json)"
CURRENT_AFTER_CLEAR_JSON="$(run_btwin runtime current --json)"
INBOX_JSON="$(run_btwin agent inbox alice --json)"

JSON_PAYLOAD_THREAD="${THREAD_JSON}" \
JSON_PAYLOAD_BIND="${RUN_BIND_JSON}" \
JSON_PAYLOAD_CURRENT="${CURRENT_JSON}" \
JSON_PAYLOAD_NEXT="${NEXT_JSON}" \
JSON_PAYLOAD_APPLY="${APPLY_JSON}" \
JSON_PAYLOAD_MAILBOX="${MAILBOX_JSON}" \
JSON_PAYLOAD_CLEARED="${CLEARED_JSON}" \
JSON_PAYLOAD_CURRENT_AFTER_CLEAR="${CURRENT_AFTER_CLEAR_JSON}" \
JSON_PAYLOAD_INBOX="${INBOX_JSON}" \
JSON_PAYLOAD_HUD="${HUD_OUTPUT}" \
BTWIN_ATTACHED_SMOKE_ROOT="${ROOT_DIR}" \
BTWIN_ATTACHED_SMOKE_THREAD_ID="${THREAD_ID}" \
HOME="${HOME_DIR}" \
uv run --project "${REPO_ROOT}" python - <<'PY'
import json
import os

thread = json.loads(os.environ["JSON_PAYLOAD_THREAD"])
bind = json.loads(os.environ["JSON_PAYLOAD_BIND"])
current = json.loads(os.environ["JSON_PAYLOAD_CURRENT"])
next_payload = json.loads(os.environ["JSON_PAYLOAD_NEXT"])
apply_payload = json.loads(os.environ["JSON_PAYLOAD_APPLY"])
mailbox = json.loads(os.environ["JSON_PAYLOAD_MAILBOX"])
cleared = json.loads(os.environ["JSON_PAYLOAD_CLEARED"])
current_after_clear = json.loads(os.environ["JSON_PAYLOAD_CURRENT_AFTER_CLEAR"])
inbox = json.loads(os.environ["JSON_PAYLOAD_INBOX"])
hud = os.environ["JSON_PAYLOAD_HUD"]

assert bind["bound"] is True, bind
assert bind["binding"]["agent_name"] == "alice", bind
assert bind["binding"]["thread_id"] == thread["thread_id"], bind
assert current["bound"] is True, current
assert current["binding"]["thread_id"] == thread["thread_id"], current
assert next_payload["suggested_action"] == "advance_phase", next_payload
assert next_payload["next_phase"] == "discussion", next_payload
assert apply_payload["applied"] is True, apply_payload
assert apply_payload["thread"]["current_phase"] == "discussion", apply_payload
assert apply_payload["thread_source"] == "runtime_binding", apply_payload
assert mailbox["count"] == 1, mailbox
assert mailbox["reports"][0]["report_type"] == "cycle_result", mailbox
assert "Cycle Feed" in hud, hud
assert "cycle report" in hud, hud
assert cleared["cleared"] is True, cleared
assert current_after_clear["bound"] is False, current_after_clear
assert current_after_clear["binding"] is None, current_after_clear
assert inbox["context"]["runtime_mode"] == "attached", inbox
assert inbox["runtime_session_error"] is None, inbox
assert inbox["attached_runtime_diagnostics"]["url"] == os.environ["BTWIN_API_URL"], inbox
assert inbox["attached_runtime_diagnostics"], inbox
assert inbox["pending_message_count"] == 1, inbox

print("Attached helper smoke passed")
print(f"- root: {os.environ['BTWIN_ATTACHED_SMOKE_ROOT']}")
print(f"- project: {os.environ['BTWIN_ATTACHED_SMOKE_ROOT']}/project")
print(f"- thread_id: {thread['thread_id']}")
print(f"- runtime binding: {bind['binding']['agent_name']} -> {bind['binding']['thread_id']}")
print(f"- protocol next: {next_payload['suggested_action']} -> {next_payload['next_phase']}")
print(f"- protocol apply-next: {apply_payload['thread']['current_phase']}")
print(f"- mailbox reports: {mailbox['count']}")
print(f"- hud cycle feed visible: {'Cycle Feed' in hud and 'cycle report' in hud}")
print(f"- runtime clear: {current_after_clear['bound']}")
print(f"- agent inbox: pending={inbox['pending_message_count']} diagnostics={bool(inbox.get('attached_runtime_diagnostics'))}")
PY
