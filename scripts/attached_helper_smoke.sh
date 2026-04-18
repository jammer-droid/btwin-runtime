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

wait_for_attached_runtime() {
  local thread_id="$1"
  local attached_ready=0
  for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
    if BTWIN_ATTACHED_SMOKE_THREAD_ID="${thread_id}" run_python - <<'PY'
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
      attached_ready=1
      break
    fi
    sleep 1
  done
  if [[ "${attached_ready}" != "1" ]]; then
    echo "Timed out waiting for attached runtime session: ${thread_id}" >&2
    exit 1
  fi
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

from tests.protocol_scenario_matrix import scenario_protocol_definition

protocol = scenario_protocol_definition("retry_same_phase")

for env_var in ("BTWIN_ATTACHED_SMOKE_DATA_PROTOCOLS_DIR", "BTWIN_ATTACHED_SMOKE_PROJECT_PROTOCOLS_DIR"):
    path = Path(os.environ[env_var]) / "review-loop.yaml"
    path.write_text(yaml.safe_dump(protocol, sort_keys=False), encoding="utf-8")
PY

echo "Running attached helper flow..."
run_btwin agent create alice --provider codex --role implementer --model gpt-5 >/dev/null
THREAD_JSON="$(run_btwin thread create --topic "Attached helper smoke" --protocol review-loop --participant alice --json)"
THREAD_ID="$(JSON_PAYLOAD="${THREAD_JSON}" run_python -c 'import json, os; print(json.loads(os.environ["JSON_PAYLOAD"])["thread_id"])')"
RUN_BIND_JSON="$(run_btwin runtime bind --thread "${THREAD_ID}" --agent alice --json)"
run_btwin live attach --thread "${THREAD_ID}" --agent alice --json >/dev/null
wait_for_attached_runtime "${THREAD_ID}"
SEED_PHASE_CYCLE_JSON="$(BTWIN_ATTACHED_SMOKE_THREAD_ID="${THREAD_ID}" run_python - <<'PY'
import json
import os
import urllib.request

base = os.environ["BTWIN_API_URL"]
thread_id = os.environ["BTWIN_ATTACHED_SMOKE_THREAD_ID"]
with urllib.request.urlopen(f"{base}/api/threads/{thread_id}/phase-cycle") as response:
    print(response.read().decode("utf-8"))
PY
)"
SEED_THREAD_WATCH_JSON="$(run_btwin thread watch "${THREAD_ID}" --limit 10 --json)"
CURRENT_JSON="$(run_btwin runtime current --json)"
run_btwin contribution submit --thread "${THREAD_ID}" --agent alice --phase review --content $'## completed\nCycle 1 ready for another pass.\n' --tldr "review cycle 1" --json >/dev/null
APPLY_ONE_JSON="$(run_btwin protocol apply-next --thread "${THREAD_ID}" --outcome retry --json)"
run_btwin contribution submit --thread "${THREAD_ID}" --agent alice --phase review --content $'## completed\nCycle 2 ready for another pass.\n' --tldr "review cycle 2" --json >/dev/null
APPLY_TWO_JSON="$(run_btwin protocol apply-next --thread "${THREAD_ID}" --outcome retry --json)"
RETRY_THREAD_WATCH_JSON="$(run_btwin thread watch "${THREAD_ID}" --limit 10 --json)"
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

BLOCKED_THREAD_JSON="$(run_btwin thread create --topic "Attached stop block smoke" --protocol review-loop --participant alice --json)"
BLOCKED_THREAD_ID="$(JSON_PAYLOAD="${BLOCKED_THREAD_JSON}" run_python -c 'import json, os; print(json.loads(os.environ["JSON_PAYLOAD"])["thread_id"])')"
run_btwin live attach --thread "${BLOCKED_THREAD_ID}" --agent alice --json >/dev/null
wait_for_attached_runtime "${BLOCKED_THREAD_ID}"
BLOCKED_STOP_HOOK_PAYLOAD="$(PROJECT_ROOT_JSON="${PROJECT_ROOT}" run_python - <<'PY'
import json
import os

print(
    json.dumps(
        {
            "session_id": "attached-smoke-blocked",
            "cwd": os.environ["PROJECT_ROOT_JSON"],
            "hook_event_name": "Stop",
            "turn_id": "turn-blocked",
        }
    )
)
PY
)"
set +e
BLOCKED_STOP_JSON="$(printf '%s\n' "${BLOCKED_STOP_HOOK_PAYLOAD}" | run_btwin workflow hook --event Stop --thread "${BLOCKED_THREAD_ID}" --agent alice --json)"
BLOCKED_STOP_STATUS=$?
set -e
if [[ "${BLOCKED_STOP_STATUS}" -ne 2 ]]; then
  echo "Expected Stop hook block for ${BLOCKED_THREAD_ID}, got exit ${BLOCKED_STOP_STATUS}." >&2
  exit 1
fi
BLOCKED_THREAD_WATCH_JSON="$(run_btwin thread watch "${BLOCKED_THREAD_ID}" --limit 10 --json)"

CLOSE_THREAD_JSON="$(run_btwin thread create --topic "Attached close path smoke" --protocol review-loop --participant alice --json)"
CLOSE_THREAD_ID="$(JSON_PAYLOAD="${CLOSE_THREAD_JSON}" run_python -c 'import json, os; print(json.loads(os.environ["JSON_PAYLOAD"])["thread_id"])')"
run_btwin live attach --thread "${CLOSE_THREAD_ID}" --agent alice --json >/dev/null
wait_for_attached_runtime "${CLOSE_THREAD_ID}"
run_btwin contribution submit --thread "${CLOSE_THREAD_ID}" --agent alice --phase review --content $'## completed\nReady to close.\n' --tldr "close path ready" --json >/dev/null
CLOSE_NEXT_JSON="$(run_btwin protocol next --thread "${CLOSE_THREAD_ID}" --outcome close --json)"
CLOSE_APPLY_JSON="$(run_btwin protocol apply-next --thread "${CLOSE_THREAD_ID}" --outcome close --json)"
CLOSE_THREAD_WATCH_JSON="$(run_btwin thread watch "${CLOSE_THREAD_ID}" --limit 10 --json)"

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
JSON_PAYLOAD_SEED_PHASE_CYCLE="${SEED_PHASE_CYCLE_JSON}" \
JSON_PAYLOAD_SEED_THREAD_WATCH="${SEED_THREAD_WATCH_JSON}" \
JSON_PAYLOAD_APPLY_ONE="${APPLY_ONE_JSON}" \
JSON_PAYLOAD_APPLY_TWO="${APPLY_TWO_JSON}" \
JSON_PAYLOAD_RETRY_THREAD_WATCH="${RETRY_THREAD_WATCH_JSON}" \
JSON_PAYLOAD_PHASE_CYCLE="${PHASE_CYCLE_JSON}" \
JSON_PAYLOAD_MAILBOX="${MAILBOX_JSON}" \
JSON_PAYLOAD_BLOCKED_STOP="${BLOCKED_STOP_JSON}" \
JSON_PAYLOAD_BLOCKED_THREAD_WATCH="${BLOCKED_THREAD_WATCH_JSON}" \
JSON_PAYLOAD_CLOSE_NEXT="${CLOSE_NEXT_JSON}" \
JSON_PAYLOAD_CLOSE_APPLY="${CLOSE_APPLY_JSON}" \
JSON_PAYLOAD_CLOSE_THREAD_WATCH="${CLOSE_THREAD_WATCH_JSON}" \
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

from tests.protocol_scenario_matrix import get_scenario

retry_scenario = get_scenario("retry_same_phase")
blocked_scenario = get_scenario("blocked_stop_missing_contribution")
close_scenario = get_scenario("close_path")
seed_scenario = get_scenario("attach_seed_first_cycle")


def latest_trace_row(payload: dict[str, object], *, kind: str) -> dict[str, object]:
    trace = payload.get("trace")
    assert isinstance(trace, list), payload
    for row in reversed(trace):
        if isinstance(row, dict) and row.get("kind") == kind:
            return row
    raise AssertionError(f"missing trace row for kind={kind}: {payload}")

thread = json.loads(os.environ["JSON_PAYLOAD_THREAD"])
bind = json.loads(os.environ["JSON_PAYLOAD_BIND"])
current = json.loads(os.environ["JSON_PAYLOAD_CURRENT"])
seed_watch = json.loads(os.environ["JSON_PAYLOAD_SEED_THREAD_WATCH"])
seed_phase_cycle = json.loads(os.environ["JSON_PAYLOAD_SEED_PHASE_CYCLE"])
apply_one = json.loads(os.environ["JSON_PAYLOAD_APPLY_ONE"])
apply_two = json.loads(os.environ["JSON_PAYLOAD_APPLY_TWO"])
retry_watch = json.loads(os.environ["JSON_PAYLOAD_RETRY_THREAD_WATCH"])
phase_cycle = json.loads(os.environ["JSON_PAYLOAD_PHASE_CYCLE"])
mailbox = json.loads(os.environ["JSON_PAYLOAD_MAILBOX"])
blocked_stop = json.loads(os.environ["JSON_PAYLOAD_BLOCKED_STOP"])
blocked_watch = json.loads(os.environ["JSON_PAYLOAD_BLOCKED_THREAD_WATCH"])
close_next = json.loads(os.environ["JSON_PAYLOAD_CLOSE_NEXT"])
close_apply = json.loads(os.environ["JSON_PAYLOAD_CLOSE_APPLY"])
close_watch = json.loads(os.environ["JSON_PAYLOAD_CLOSE_THREAD_WATCH"])
cleared = json.loads(os.environ["JSON_PAYLOAD_CLEARED"])
current_after_clear = json.loads(os.environ["JSON_PAYLOAD_CURRENT_AFTER_CLEAR"])
mailbox_after_clear = json.loads(os.environ["JSON_PAYLOAD_MAILBOX_AFTER_CLEAR"])
inbox = json.loads(os.environ["JSON_PAYLOAD_INBOX"])
hud = os.environ["JSON_PAYLOAD_HUD"]

seed_watch_phase_cycle = seed_watch["phase_cycle"]
retry_gate = latest_trace_row(retry_watch, kind="gate")
blocked_guard = latest_trace_row(blocked_watch, kind="guard")
close_gate = latest_trace_row(close_watch, kind="gate")

assert bind["bound"] is True, bind
assert bind["binding"]["agent_name"] == "alice", bind
assert bind["binding"]["thread_id"] == thread["thread_id"], bind
assert current["bound"] is True, current
assert current["binding"]["thread_id"] == thread["thread_id"], current
assert seed_scenario.preview_status == "valid", seed_scenario
assert seed_scenario.live_smoke_required is True, seed_scenario
assert isinstance(seed_phase_cycle, dict), seed_phase_cycle
assert seed_phase_cycle["state"]["cycle_index"] == 1, seed_phase_cycle
assert seed_phase_cycle["state"]["phase_name"] == "review", seed_phase_cycle
assert seed_phase_cycle["context_core"]["current_step_alias"] == "Review", seed_phase_cycle
assert seed_phase_cycle["context_core"]["next_expected_role"] == "reviewer", seed_phase_cycle
assert seed_phase_cycle["visual"]["procedure"][0]["key"] == seed_scenario.procedure_key, seed_phase_cycle
assert seed_phase_cycle["visual"]["gates"][0]["key"] == seed_scenario.gate_key, seed_phase_cycle
assert seed_phase_cycle["visual"]["gates"][0]["target_phase"] == seed_scenario.target_phase, seed_phase_cycle
assert isinstance(seed_watch_phase_cycle, dict), seed_watch
assert seed_watch_phase_cycle["state"]["cycle_index"] == 1, seed_watch_phase_cycle
assert seed_watch_phase_cycle["context_core"]["current_step_alias"] == "Review", seed_watch_phase_cycle
assert "synthetic" not in seed_watch_phase_cycle, seed_watch_phase_cycle
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
assert phase_cycle["visual"]["gates"][0] == retry_scenario.visual_gates[0].as_dict(), phase_cycle
assert phase_cycle["visual"]["gates"][1] == retry_scenario.visual_gates[1].as_dict(), phase_cycle
assert retry_gate["outcome"] == retry_scenario.outcome, retry_gate
assert retry_gate["gate_key"] == retry_scenario.gate_key, retry_gate
assert retry_gate["target_phase"] == retry_scenario.target_phase, retry_gate
assert retry_gate["procedure_key"] == retry_scenario.procedure_key, retry_gate
assert retry_gate["cycle_index"] == 2, retry_gate
assert retry_gate["next_cycle_index"] == 3, retry_gate
assert retry_gate["outcome_policy"] == retry_scenario.outcome_policy, retry_gate
assert retry_gate["outcome_emitters"] == list(retry_scenario.outcome_emitters), retry_gate
assert retry_gate["outcome_actions"] == list(retry_scenario.outcome_actions), retry_gate
assert retry_gate["policy_outcomes"] == list(retry_scenario.policy_outcomes), retry_gate
assert mailbox["count"] == 2, mailbox
assert [report["cycle_index"] for report in mailbox["reports"]] == [2, 1], mailbox
assert [report["next_cycle_index"] for report in mailbox["reports"]] == [3, 2], mailbox
assert mailbox["reports"][0]["report_type"] == "cycle_result", mailbox
assert blocked_scenario.preview_status == "note", blocked_scenario
assert blocked_stop["decision"] == "block", blocked_stop
assert blocked_stop["reason"] == "missing_contribution", blocked_stop
assert blocked_stop["details"]["guard_source"] == "baseline", blocked_stop
assert blocked_guard["event_type"] == "phase_exit_blocked", blocked_guard
assert blocked_guard["reason"] == "missing_contribution", blocked_guard
assert blocked_guard["baseline_guard"] == "contribution_required", blocked_guard
assert blocked_guard["hook_event_name"] == "Stop", blocked_guard
assert "baseline runtime guard remains always-on" in str(blocked_guard["summary"]), blocked_guard
assert close_scenario.preview_status == "valid", close_scenario
assert close_next["requested_outcome"] == close_scenario.outcome, close_next
assert close_next["next_phase"] == close_scenario.target_phase, close_next
assert close_apply["applied"] is True, close_apply
assert close_apply["suggested_action"] == "advance_phase", close_apply
assert close_apply["thread"]["current_phase"] == "decision", close_apply
assert close_gate["outcome"] == close_scenario.outcome, close_gate
assert close_gate["gate_key"] == close_scenario.gate_key, close_gate
assert close_gate["target_phase"] == close_scenario.target_phase, close_gate
assert close_gate["procedure_key"] == close_scenario.procedure_key, close_gate
assert close_gate["cycle_index"] == 1, close_gate
assert close_gate["next_cycle_index"] == 1, close_gate
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
print(
    f"- thread-watch retry trace: outcome={retry_gate['outcome']} gate={retry_gate['gate_key']} "
    f"target={retry_gate['target_phase']} cycle={retry_gate['cycle_index']}->{retry_gate['next_cycle_index']}"
)
print(
    f"- thread-watch retry policy: policy={retry_gate['outcome_policy']} "
    f"emitters={','.join(retry_gate['outcome_emitters'])} "
    f"actions={','.join(retry_gate['outcome_actions'])} "
    f"outcomes={','.join(retry_gate['policy_outcomes'])}"
)
print(
    f"- thread-watch blocked stop: reason={blocked_guard['reason']} "
    f"baseline={blocked_guard['baseline_guard']}"
)
print(
    f"- thread-watch close trace: outcome={close_gate['outcome']} gate={close_gate['gate_key']} "
    f"target={close_gate['target_phase']} cycle={close_gate['cycle_index']}->{close_gate['next_cycle_index']}"
)
print(
    f"- thread-watch seed trace: cycle={seed_watch_phase_cycle['state']['cycle_index']} "
    f"procedure={seed_watch_phase_cycle['visual']['procedure'][0]['key']} "
    f"target={seed_watch_phase_cycle['visual']['gates'][0]['target_phase']}"
)
print(f"- mailbox reports: {mailbox['count']}")
print(f"- hud cycle feed visible: {'Cycle Feed' in hud and 'cycle report' in hud}")
print(f"- hud protocol progress visible: {'Protocol Progress' in hud}")
print(f"- hud procedure visible: {'Procedure' in hud}")
print(f"- hud gate visuals visible: {'Gates' in hud and 'Retry Gate' in hud and 'Accept Gate' in hud}")
print(f"- runtime clear: {current_after_clear['bound']}")
print(f"- mailbox reports after clear: {mailbox_after_clear['count']}")
print(f"- agent inbox: pending={inbox['pending_message_count']} diagnostics={bool(inbox.get('attached_runtime_diagnostics'))}")
PY
