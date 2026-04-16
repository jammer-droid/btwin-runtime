#!/usr/bin/env bash

set -euo pipefail

DRY_RUN=0

print_help() {
  cat <<'EOF'
Usage: ./scripts/install_btwin_macos.sh [--dry-run]

One-shot macOS installer for a fresh btwin repository clone.

This script:
  1. runs `uv sync` in the repository
  2. installs `btwin` globally with `uv tool install -e .`
  3. runs `btwin init`
  4. runs `btwin service install`
  5. shows the final Codex restart reminder

Requirements:
  - macOS
  - `uv` installed and on PATH
  - `codex` installed and on PATH

Options:
  --dry-run   Print the commands without executing them
  -h, --help  Show this help text
EOF
}

fail() {
  printf 'error: %s\n' "$1" >&2
  exit 1
}

log() {
  printf '[btwin-install] %s\n' "$1"
}

run_cmd() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '+'
    for arg in "$@"; do
      printf ' %q' "$arg"
    done
    printf '\n'
    return 0
  fi
  "$@"
}

run_repo_cmd() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '+ (cd %q &&' "$REPO_ROOT"
    for arg in "$@"; do
      printf ' %q' "$arg"
    done
    printf ')\n'
    return 0
  fi
  (
    cd "$REPO_ROOT"
    "$@"
  )
}

for arg in "$@"; do
  case "$arg" in
    --dry-run)
      DRY_RUN=1
      ;;
    -h|--help)
      print_help
      exit 0
      ;;
    *)
      fail "unknown argument: $arg"
      ;;
  esac
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
GLOBAL_BTWIN_DATA_DIR="$HOME/.btwin"
GLOBAL_BTWIN_CONFIG_PATH="$GLOBAL_BTWIN_DATA_DIR/config.yaml"
LOGIN_SHELL="${SHELL:-/bin/zsh}"

if [[ ! -f "$REPO_ROOT/pyproject.toml" ]]; then
  fail "could not find pyproject.toml under $REPO_ROOT"
fi

if [[ "$DRY_RUN" -eq 0 && "$(uname -s)" != "Darwin" ]]; then
  fail "this installer currently supports macOS only"
fi

command -v uv >/dev/null 2>&1 || fail "uv is not installed. Install uv first, then rerun this script."
command -v codex >/dev/null 2>&1 || fail "codex is not installed. Install codex first, then rerun this script."

log "Repository: $REPO_ROOT"
log "Step 1/4: sync the repo-local environment"
run_repo_cmd uv sync

log "Step 2/4: install btwin globally"
run_cmd uv tool install -e "$REPO_ROOT"

export PATH="$HOME/.local/bin:$PATH"

BTWIN_BIN="$(command -v btwin || true)"
if [[ -z "$BTWIN_BIN" && -x "$HOME/.local/bin/btwin" ]]; then
  BTWIN_BIN="$HOME/.local/bin/btwin"
fi
[[ -n "$BTWIN_BIN" ]] || fail "btwin was not found on PATH after installation. Try `uv tool update-shell` and rerun."

run_global_btwin_cmd() {
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '+ BTWIN_DATA_DIR=%q BTWIN_CONFIG_PATH=%q %q' \
      "$GLOBAL_BTWIN_DATA_DIR" \
      "$GLOBAL_BTWIN_CONFIG_PATH" \
      "$1"
    shift
    for arg in "$@"; do
      printf ' %q' "$arg"
    done
    printf '\n'
    return 0
  fi
  BTWIN_DATA_DIR="$GLOBAL_BTWIN_DATA_DIR" \
  BTWIN_CONFIG_PATH="$GLOBAL_BTWIN_CONFIG_PATH" \
    "$@"
}

run_global_btwin_cmd_fresh_shell() {
  local btwin_bin="$1"
  shift
  if [[ "$DRY_RUN" -eq 1 ]]; then
    printf '+ %q -lc "%s"\n' "$LOGIN_SHELL" "BTWIN_DATA_DIR=\"$GLOBAL_BTWIN_DATA_DIR\" BTWIN_CONFIG_PATH=\"$GLOBAL_BTWIN_CONFIG_PATH\" \"$btwin_bin\" $*"
    return 0
  fi
  "$LOGIN_SHELL" -lc "BTWIN_DATA_DIR=\"$GLOBAL_BTWIN_DATA_DIR\" BTWIN_CONFIG_PATH=\"$GLOBAL_BTWIN_CONFIG_PATH\" \"$btwin_bin\" $*"
}

retry_service_install() {
  local btwin_bin="$1"
  if [[ "$DRY_RUN" -eq 1 ]]; then
    run_global_btwin_cmd_fresh_shell "$btwin_bin" service install
    return 0
  fi

  local attempt
  for attempt in 1 2; do
    if run_global_btwin_cmd_fresh_shell "$btwin_bin" service install; then
      return 0
    fi
    if [[ "$attempt" -lt 2 ]]; then
      log "service install failed once; retrying in a fresh login shell"
      sleep 1
    fi
  done
  return 1
}

log "Step 3/4: initialize the global Codex-facing btwin setup"
run_global_btwin_cmd "$BTWIN_BIN" init

log "Step 4/4: install the macOS background service"
# launchctl bootstrap is more reliable here when invoked from a fresh shell
# after the install/init steps above.
retry_service_install "$BTWIN_BIN"
run_global_btwin_cmd_fresh_shell "$BTWIN_BIN" service status

cat <<'EOF'

Done.

Next:
  1. Restart Codex so it reconnects with the new MCP config.
  2. Open Codex in the repository where you want to use btwin.
  3. Let btwin lazily create helper overlay files when it launches a managed helper.
EOF
