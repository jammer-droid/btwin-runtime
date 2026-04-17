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

DATA_PROTOCOLS_DIR="${BTWIN_DATA_DIR}/protocols"
PROJECT_PROTOCOLS_DIR="${PROJECT_ROOT}/.btwin/protocols"
mkdir -p "${DATA_PROTOCOLS_DIR}" "${PROJECT_PROTOCOLS_DIR}"
BTWIN_ATTACHED_SMOKE_DATA_PROTOCOLS_DIR="${DATA_PROTOCOLS_DIR}" \
BTWIN_ATTACHED_SMOKE_PROJECT_PROTOCOLS_DIR="${PROJECT_PROTOCOLS_DIR}" \
run_python - <<'PY'
import os
from pathlib import Path

import yaml

protocol = {
    "name": "attached-helper-smoke",
    "description": "Minimal protocol for attached helper smoke",
    "outcomes": ["retry", "accept"],
    "phases": [
        {
            "name": "review",
            "actions": ["contribute", "discuss"],
            "template": [{"section": "completed", "required": True}],
            "procedure": [
                {"key": "review-pass", "role": "reviewer", "action": "review", "alias": "Review"},
                {"key": "revise-pass", "role": "implementer", "action": "revise", "alias": "Revise"},
            ],
        },
        {
            "name": "decision",
            "actions": ["discuss"],
        },
    ],
    "transitions": [
        {"key": "retry-loop", "from": "review", "to": "review", "on": "retry", "alias": "Retry Gate"},
        {"key": "accept-loop", "from": "review", "to": "decision", "on": "accept", "alias": "Accept Gate"},
    ],
}

for env_var in ("BTWIN_ATTACHED_SMOKE_DATA_PROTOCOLS_DIR", "BTWIN_ATTACHED_SMOKE_PROJECT_PROTOCOLS_DIR"):
    path = Path(os.environ[env_var]) / "attached-helper-smoke.yaml"
    path.write_text(yaml.safe_dump(protocol, sort_keys=False), encoding="utf-8")
PY

echo "Running attached helper flow..."
run_btwin agent create alice --provider codex --role implementer --model gpt-5 >/dev/null
THREAD_JSON="$(run_btwin thread create --topic "Attached helper smoke" --protocol attached-helper-smoke --participant alice --json)"
THREAD_ID="$(JSON_PAYLOAD="${THREAD_JSON}" run_python -c 'import json, os; print(json.loads(os.environ["JSON_PAYLOAD"])["thread_id"])')"
RUN_BIND_JSON="$(run_btwin runtime bind --thread "${THREAD_ID}" --agent alice --json)"
run_btwin live attach --thread "${THREAD_ID}" --agent alice --json >/dev/null
ATTACHED_READY=0
for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
  if BTWIN_ATTACHED_SMOKE_THREAD_ID="${THREAD_ID}" run_python - <<'PY'
import json
import os
import urllib.request

base = os.environ["BTWIN_API_URL"]
thread_id = os.environ["BTWIN_ATTACHED_SMOKE_THREAD_ID"]
with urllib.request.urlopen(f"{base}/api/agent-runtime-status") as response:
    payload = json.loads(response.read().decode("utf-8"))
agents = payload.get("agents", {})
for session in agents.get("alice", []):
    if session.get("thread_id") == thread_id:
        raise SystemExit(0)
raise SystemExit(1)
PY
  then
    ATTACHED_READY=1
    break
  fi
  sleep 1
done
if [[ "${ATTACHED_READY}" != "1" ]]; then
  echo "Timed out waiting for attached runtime session." >&2
  exit 1
fi
CURRENT_JSON="$(run_btwin runtime current --json)"
run_btwin contribution submit --thread "${THREAD_ID}" --agent alice --phase review --content $'## completed\nCycle 1 ready for another pass.\n' --tldr "review cycle 1" --json >/dev/null
APPLY_ONE_JSON="$(run_btwin protocol apply-next --thread "${THREAD_ID}" --outcome retry --json)"
run_btwin contribution submit --thread "${THREAD_ID}" --agent alice --phase review --content $'## completed\nCycle 2 ready for another pass.\n' --tldr "review cycle 2" --json >/dev/null
APPLY_TWO_JSON="$(run_btwin protocol apply-next --thread "${THREAD_ID}" --outcome retry --json)"
PHASE_CYCLE_JSON="$(BTWIN_ATTACHED_SMOKE_THREAD_ID="${THREAD_ID}" run_python - <<'PY'
import json
import os
import urllib.request

base = os.environ["BTWIN_API_URL"]
thread_id = os.environ["BTWIN_ATTACHED_SMOKE_THREAD_ID"]
with urllib.request.urlopen(f"{base}/api/threads/{thread_id}/phase-cycle") as response:
    print(response.read().decode("utf-8"))
PY
)"
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
MAILBOX_AFTER_CLEAR_JSON="$(BTWIN_ATTACHED_SMOKE_THREAD_ID="${THREAD_ID}" run_python - <<'PY'
import json
import os
import urllib.request

base = os.environ["BTWIN_API_URL"]
thread_id = os.environ["BTWIN_ATTACHED_SMOKE_THREAD_ID"]
with urllib.request.urlopen(f"{base}/api/system-mailbox?threadId={thread_id}&limit=5") as response:
    print(response.read().decode("utf-8"))
PY
)"
INBOX_JSON="$(run_btwin agent inbox alice --json)"

JSON_PAYLOAD_THREAD="${THREAD_JSON}" \
JSON_PAYLOAD_BIND="${RUN_BIND_JSON}" \
JSON_PAYLOAD_CURRENT="${CURRENT_JSON}" \
JSON_PAYLOAD_APPLY_ONE="${APPLY_ONE_JSON}" \
JSON_PAYLOAD_APPLY_TWO="${APPLY_TWO_JSON}" \
JSON_PAYLOAD_PHASE_CYCLE="${PHASE_CYCLE_JSON}" \
JSON_PAYLOAD_MAILBOX="${MAILBOX_JSON}" \
JSON_PAYLOAD_CLEARED="${CLEARED_JSON}" \
JSON_PAYLOAD_CURRENT_AFTER_CLEAR="${CURRENT_AFTER_CLEAR_JSON}" \
JSON_PAYLOAD_MAILBOX_AFTER_CLEAR="${MAILBOX_AFTER_CLEAR_JSON}" \
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
apply_one = json.loads(os.environ["JSON_PAYLOAD_APPLY_ONE"])
apply_two = json.loads(os.environ["JSON_PAYLOAD_APPLY_TWO"])
phase_cycle = json.loads(os.environ["JSON_PAYLOAD_PHASE_CYCLE"])
mailbox = json.loads(os.environ["JSON_PAYLOAD_MAILBOX"])
cleared = json.loads(os.environ["JSON_PAYLOAD_CLEARED"])
current_after_clear = json.loads(os.environ["JSON_PAYLOAD_CURRENT_AFTER_CLEAR"])
mailbox_after_clear = json.loads(os.environ["JSON_PAYLOAD_MAILBOX_AFTER_CLEAR"])
inbox = json.loads(os.environ["JSON_PAYLOAD_INBOX"])
hud = os.environ["JSON_PAYLOAD_HUD"]

assert bind["bound"] is True, bind
assert bind["binding"]["agent_name"] == "alice", bind
assert bind["binding"]["thread_id"] == thread["thread_id"], bind
assert current["bound"] is True, current
assert current["binding"]["thread_id"] == thread["thread_id"], current
assert apply_one["applied"] is True, apply_one
assert apply_one["cycle"]["cycle_index"] == 2, apply_one
assert apply_one["thread"]["current_phase"] == "review", apply_one
assert apply_one["thread_source"] == "explicit", apply_one
assert apply_two["applied"] is True, apply_two
assert apply_two["cycle"]["cycle_index"] == 3, apply_two
assert apply_two["thread"]["current_phase"] == "review", apply_two
assert apply_two["thread_source"] == "explicit", apply_two
assert apply_two["context_core"]["next_expected_role"] == "reviewer", apply_two
assert apply_two["context_core"]["current_step_alias"] == "Review", apply_two
assert apply_two["context_core"]["current_step_role"] == "reviewer", apply_two
assert phase_cycle["state"]["cycle_index"] == 3, phase_cycle
assert phase_cycle["state"]["phase_name"] == "review", phase_cycle
assert phase_cycle["context_core"]["next_expected_role"] == "reviewer", phase_cycle
assert phase_cycle["context_core"]["current_step_alias"] == "Review", phase_cycle
assert phase_cycle["context_core"]["current_step_role"] == "reviewer", phase_cycle
assert phase_cycle["visual"]["procedure"][0]["key"] == "review-pass", phase_cycle
assert phase_cycle["visual"]["procedure"][0]["label"] == "Review", phase_cycle
assert phase_cycle["visual"]["procedure"][1]["key"] == "revise-pass", phase_cycle
assert phase_cycle["visual"]["procedure"][1]["label"] == "Revise", phase_cycle
assert phase_cycle["visual"]["gates"][0]["key"] == "retry-loop", phase_cycle
assert phase_cycle["visual"]["gates"][0]["label"] == "Retry Gate", phase_cycle
assert phase_cycle["visual"]["gates"][0]["target_phase"] == "review", phase_cycle
assert phase_cycle["visual"]["gates"][1]["key"] == "accept-loop", phase_cycle
assert phase_cycle["visual"]["gates"][1]["label"] == "Accept Gate", phase_cycle
assert phase_cycle["visual"]["gates"][1]["target_phase"] == "decision", phase_cycle
assert mailbox["count"] == 2, mailbox
assert [report["cycle_index"] for report in mailbox["reports"]] == [2, 1], mailbox
assert [report["next_cycle_index"] for report in mailbox["reports"]] == [3, 2], mailbox
assert mailbox["reports"][0]["report_type"] == "cycle_result", mailbox
assert "Cycle Feed" in hud, hud
assert "cycle report" in hud, hud
assert "Protocol Progress" in hud, hud
assert "Procedure" in hud, hud
assert "Gates" in hud, hud
assert "Review" in hud, hud
assert "Revise" in hud, hud
assert "Retry Gate" in hud, hud
assert "Accept Gate" in hud, hud
assert cleared["cleared"] is True, cleared
assert current_after_clear["bound"] is False, current_after_clear
assert current_after_clear["binding"] is None, current_after_clear
assert mailbox_after_clear["count"] == 2, mailbox_after_clear
assert inbox["context"]["runtime_mode"] == "attached", inbox
assert inbox["runtime_session_error"] is None, inbox
assert inbox["attached_runtime_diagnostics"]["url"] == os.environ["BTWIN_API_URL"], inbox
assert inbox["attached_runtime_diagnostics"], inbox
assert inbox["pending_message_count"] == 0, inbox

print("Attached helper smoke passed")
print(f"- root: {os.environ['BTWIN_ATTACHED_SMOKE_ROOT']}")
print(f"- project: {os.environ['BTWIN_ATTACHED_SMOKE_ROOT']}/project")
print(f"- thread_id: {thread['thread_id']}")
print(f"- runtime binding: {bind['binding']['agent_name']} -> {bind['binding']['thread_id']}")
print(f"- protocol apply-next cycle 1: {apply_one['thread']['current_phase']}")
print(f"- protocol apply-next cycle 2: {apply_two['thread']['current_phase']}")
print(f"- phase-cycle api visible: {phase_cycle['state']['cycle_index'] == 3 and phase_cycle['visual']['procedure'][0]['key'] == 'review-pass'}")
print(f"- phase-cycle next role: {phase_cycle['context_core']['next_expected_role']}")
print(f"- phase-cycle step alias: {phase_cycle['context_core']['current_step_alias']}")
print(f"- phase-cycle step key: {phase_cycle['visual']['procedure'][0]['key']}")
print(f"- phase-cycle gate key: {phase_cycle['visual']['gates'][0]['key']}")
print(f"- mailbox reports: {mailbox['count']}")
print(f"- hud cycle feed visible: {'Cycle Feed' in hud and 'cycle report' in hud}")
print(f"- hud protocol progress visible: {'Protocol Progress' in hud}")
print(f"- hud procedure visible: {'Procedure' in hud}")
print(f"- hud gate visuals visible: {'Gates' in hud and 'Retry Gate' in hud and 'Accept Gate' in hud}")
print(f"- runtime clear: {current_after_clear['bound']}")
print(f"- mailbox reports after clear: {mailbox_after_clear['count']}")
print(f"- agent inbox: pending={inbox['pending_message_count']} diagnostics={bool(inbox.get('attached_runtime_diagnostics'))}")
PY
