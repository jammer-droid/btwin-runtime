#!/usr/bin/env bash
set -euo pipefail

SCRIPT_PATH="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/$(basename "${BASH_SOURCE[0]}")"
SCRIPT_NAME="$(basename "${BASH_SOURCE[0]}")"

PGREP_BIN="${CODEX_SESSION_PGREP_BIN:-pgrep}"
PS_BIN="${CODEX_SESSION_PS_BIN:-ps}"
LSOF_BIN="${CODEX_SESSION_LSOF_BIN:-lsof}"
KILL_BIN="${CODEX_SESSION_KILL_BIN:-kill}"
LAUNCHCTL_BIN="${CODEX_SESSION_LAUNCHCTL_BIN:-launchctl}"
CODEX_BIN="${CODEX_BIN:-codex}"
DEFAULT_MIN_LIMIT="${CODEX_SESSION_MIN_LIMIT:-1024}"

usage() {
  cat <<'EOF'
Usage:
  codex_session_health.sh status
  codex_session_health.sh warn
  codex_session_health.sh cleanup (--orphans | --pid PID) [--dry-run]
  codex_session_health.sh safe-start [--min-limit N] [--] [codex args...]
  codex_session_health.sh install-local [--bin-dir PATH]

Installed helper names:
  codex-safe-start
  codex-health
  codex-clean-stale
EOF
}

trim() {
  awk '{$1=$1; print}'
}

safe_run() {
  "$@" 2>/dev/null || true
}

soft_limit() {
  if [[ -n "${CODEX_SESSION_SOFT_LIMIT:-}" ]]; then
    printf '%s\n' "${CODEX_SESSION_SOFT_LIMIT}"
    return 0
  fi
  ulimit -n
}

launchctl_limit_line() {
  if [[ -n "${CODEX_SESSION_LAUNCHCTL_OUTPUT:-}" ]]; then
    printf '%s\n' "${CODEX_SESSION_LAUNCHCTL_OUTPUT}"
    return 0
  fi
  safe_run "${LAUNCHCTL_BIN}" limit maxfiles | head -n 1
}

extract_launchctl_soft_limit() {
  local line
  line="$(launchctl_limit_line)"
  if [[ -z "${line}" ]]; then
    printf 'unknown\n'
    return 0
  fi
  printf '%s\n' "${line}" | awk '{print $2}'
}

dedupe_lines_by_pid() {
  awk '
    {
      pid=$1
      if (!(pid in seen)) {
        seen[pid]=1
        print
      }
    }
  '
}

list_codex_lines() {
  safe_run "${PGREP_BIN}" -fal 'codex --sandbox|/opt/homebrew/bin/codex|/bin/zsh -ic exec codex|(^|/)codex( |$)' | dedupe_lines_by_pid
}

list_btwin_mcp_lines() {
  safe_run "${PGREP_BIN}" -fal 'btwin mcp-proxy' | dedupe_lines_by_pid
}

list_pencil_lines() {
  safe_run "${PGREP_BIN}" -fal 'visual_studio_code/out/mcp-server-darwin-arm64' | dedupe_lines_by_pid
}

count_lines() {
  awk 'NF { count += 1 } END { print count + 0 }'
}

fd_count_for_pid() {
  local pid="$1"
  safe_run "${LSOF_BIN}" -p "${pid}" | awk 'NR > 1 { count += 1 } END { print count + 0 }'
}

ps_summary_for_pid() {
  local pid="$1"
  safe_run "${PS_BIN}" -o pid=,tty=,etime=,command= -p "${pid}" | head -n 1 | trim
}

ppid_for_pid() {
  local pid="$1"
  safe_run "${PS_BIN}" -o ppid= -p "${pid}" | head -n 1 | trim
}

children_for_pid() {
  local pid="$1"
  safe_run "${PGREP_BIN}" -P "${pid}" -fal | dedupe_lines_by_pid
}

print_status_core() {
  local soft_limit_value launchctl_soft codex_lines btwin_lines pencil_lines
  local codex_count btwin_count pencil_count max_fd risk

  soft_limit_value="$(soft_limit)"
  launchctl_soft="$(extract_launchctl_soft_limit)"
  codex_lines="$(list_codex_lines)"
  btwin_lines="$(list_btwin_mcp_lines)"
  pencil_lines="$(list_pencil_lines)"

  codex_count="$(printf '%s\n' "${codex_lines}" | count_lines)"
  btwin_count="$(printf '%s\n' "${btwin_lines}" | count_lines)"
  pencil_count="$(printf '%s\n' "${pencil_lines}" | count_lines)"
  max_fd=0

  while IFS= read -r line; do
    local pid fd_count
    [[ -n "${line}" ]] || continue
    pid="$(printf '%s\n' "${line}" | awk '{print $1}')"
    fd_count="$(fd_count_for_pid "${pid}")"
    if (( fd_count > max_fd )); then
      max_fd="${fd_count}"
    fi
  done <<< "${codex_lines}"

  risk="low"
  if [[ "${soft_limit_value}" =~ ^[0-9]+$ ]]; then
    if (( soft_limit_value < 1024 )); then
      risk="high"
    elif (( soft_limit_value < 2048 )); then
      risk="medium"
    fi

    if (( max_fd >= soft_limit_value - 16 )); then
      risk="high"
    fi
  fi

  if (( codex_count > 0 )) && (( btwin_count > codex_count * 2 || pencil_count > codex_count * 2 )); then
    risk="high"
  fi

  printf 'soft_limit=%s\n' "${soft_limit_value}"
  printf 'launchctl_soft_limit=%s\n' "${launchctl_soft}"
  printf 'codex_count=%s\n' "${codex_count}"
  printf 'btwin_mcp_count=%s\n' "${btwin_count}"
  printf 'pencil_count=%s\n' "${pencil_count}"
  printf 'max_codex_fd=%s\n' "${max_fd}"
  printf 'risk=%s\n' "${risk}"

  while IFS= read -r line; do
    local pid summary fd_count
    [[ -n "${line}" ]] || continue
    pid="$(printf '%s\n' "${line}" | awk '{print $1}')"
    summary="$(ps_summary_for_pid "${pid}")"
    fd_count="$(fd_count_for_pid "${pid}")"
    printf 'candidate pid=%s fd_count=%s summary=%s\n' "${pid}" "${fd_count}" "${summary}"
  done <<< "${codex_lines}"
}

status_cmd() {
  print_status_core
}

warn_cmd() {
  print_status_core
  cat <<'EOF'
recommendation=If MCP startup or fork failures are already happening, save a handoff and restart in a fresh Codex session instead of retrying in-place.
hint=status is read-only; cleanup requires --pid PID or --orphans.
EOF
}

terminate_pid() {
  local pid="$1"
  "${KILL_BIN}" -TERM "${pid}" >/dev/null 2>&1 || true
}

cleanup_orphans_cmd() {
  local dry_run="${1:-0}"
  local combined pid parent

  combined="$(
    {
      list_btwin_mcp_lines
      list_pencil_lines
    } | dedupe_lines_by_pid
  )"

  while IFS= read -r line; do
    [[ -n "${line}" ]] || continue
    pid="$(printf '%s\n' "${line}" | awk '{print $1}')"
    parent="$(ppid_for_pid "${pid}")"
    if [[ -z "${parent}" ]] || ! ps_summary_for_pid "${parent}" | grep -qi 'codex'; then
      if [[ "${dry_run}" == "1" ]]; then
        printf 'Would terminate orphan child pid=%s parent=%s cmd=%s\n' "${pid}" "${parent:-unknown}" "${line}"
      else
        printf 'Terminating orphan child pid=%s parent=%s cmd=%s\n' "${pid}" "${parent:-unknown}" "${line}"
        terminate_pid "${pid}"
      fi
    fi
  done <<< "${combined}"
}

cleanup_pid_cmd() {
  local target_pid="$1"
  local dry_run="${2:-0}"
  local children child_pid

  if [[ -z "${target_pid}" ]]; then
    echo "cleanup requires --pid PID or --orphans" >&2
    return 2
  fi

  if [[ "${dry_run}" == "1" ]]; then
    printf 'Would terminate Codex session pid=%s\n' "${target_pid}"
  else
    printf 'Terminating Codex session pid=%s\n' "${target_pid}"
    terminate_pid "${target_pid}"
  fi

  children="$(children_for_pid "${target_pid}")"
  while IFS= read -r line; do
    [[ -n "${line}" ]] || continue
    child_pid="$(printf '%s\n' "${line}" | awk '{print $1}')"
    if [[ "${dry_run}" == "1" ]]; then
      printf 'Would terminate child pid=%s cmd=%s\n' "${child_pid}" "${line}"
    else
      printf 'Terminating child pid=%s cmd=%s\n' "${child_pid}" "${line}"
      terminate_pid "${child_pid}"
    fi
  done <<< "${children}"
}

cleanup_cmd() {
  local target_pid=""
  local cleanup_orphans=0
  local dry_run=0

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --pid)
        target_pid="${2:-}"
        shift 2
        ;;
      --orphans)
        cleanup_orphans=1
        shift
        ;;
      --dry-run)
        dry_run=1
        shift
        ;;
      --help|-h)
        usage
        return 0
        ;;
      *)
        echo "Unknown cleanup argument: $1" >&2
        return 2
        ;;
    esac
  done

  if [[ "${cleanup_orphans}" == "1" ]]; then
    cleanup_orphans_cmd "${dry_run}"
    return 0
  fi

  if [[ -z "${target_pid}" ]]; then
    echo "cleanup requires --pid PID or --orphans" >&2
    return 2
  fi

  cleanup_pid_cmd "${target_pid}" "${dry_run}"
}

safe_start_cmd() {
  local min_limit="${DEFAULT_MIN_LIMIT}"

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --min-limit)
        min_limit="${2:-}"
        shift 2
        ;;
      --help|-h)
        cat <<'EOF'
Usage: codex-safe-start [--min-limit N] [--] [codex args...]

Raises the soft open-files limit for this shell process when possible, then execs Codex.
EOF
        return 0
        ;;
      --)
        shift
        break
        ;;
      *)
        break
        ;;
    esac
  done

  if [[ "$(soft_limit)" =~ ^[0-9]+$ ]] && (( "$(soft_limit)" < min_limit )); then
    if ! ulimit -n "${min_limit}" 2>/dev/null; then
      echo "Warning: could not raise soft limit to ${min_limit}" >&2
    fi
  fi

  exec "${CODEX_BIN}" "$@"
}

install_local_cmd() {
  local bin_dir="${HOME}/.local/bin"
  local target

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --bin-dir)
        bin_dir="${2:-}"
        shift 2
        ;;
      --help|-h)
        cat <<'EOF'
Usage: codex_session_health.sh install-local [--bin-dir PATH]

Installs symlinked helper entrypoints into the target bin directory.
EOF
        return 0
        ;;
      *)
        echo "Unknown install-local argument: $1" >&2
        return 2
        ;;
    esac
  done

  mkdir -p "${bin_dir}"
  for target in codex-safe-start codex-health codex-clean-stale; do
    ln -sfn "${SCRIPT_PATH}" "${bin_dir}/${target}"
    printf 'Installed %s -> %s\n' "${bin_dir}/${target}" "${SCRIPT_PATH}"
  done
}

dispatch() {
  local command="${1:-}"
  shift || true

  case "${command}" in
    status) status_cmd "$@" ;;
    warn) warn_cmd "$@" ;;
    cleanup) cleanup_cmd "$@" ;;
    safe-start) safe_start_cmd "$@" ;;
    install-local) install_local_cmd "$@" ;;
    ""|--help|-h) usage ;;
    *)
      echo "Unknown command: ${command}" >&2
      usage >&2
      return 2
      ;;
  esac
}

case "${SCRIPT_NAME}" in
  codex-safe-start)
    safe_start_cmd "$@"
    ;;
  codex-health)
    warn_cmd "$@"
    ;;
  codex-clean-stale)
    if [[ $# -eq 0 ]]; then
      cleanup_cmd --orphans
    else
      cleanup_cmd "$@"
    fi
    ;;
  *)
    dispatch "$@"
    ;;
esac
