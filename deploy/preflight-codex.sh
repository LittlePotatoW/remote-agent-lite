#!/usr/bin/env bash
set -euo pipefail

CODEX_BIN="${CODEX_BIN:-/opt/remote-agent-lite/codex/bin/codex}"
CODEX_HOME_DIR="${CODEX_HOME_DIR:-/var/lib/remote-agent-lite/codex-home}"
CODEX_RUN_USER="${CODEX_RUN_USER:-remoteagent-codex}"
WORKDIR="${WORKDIR:-/srv/remote-agent-lite}"

if [[ ! -x "$CODEX_BIN" ]]; then
  echo "Codex binary not found: $CODEX_BIN" >&2
  exit 2
fi

run_codex() {
  local extra=("$@")
  sudo -u "$CODEX_RUN_USER" env \
    HOME="$CODEX_HOME_DIR" \
    CODEX_HOME="$CODEX_HOME_DIR" \
    DSAPI_API_KEY="${DSAPI_API_KEY:-}" \
    "$CODEX_BIN" exec \
    --skip-git-repo-check \
    -C "$WORKDIR" \
    "${extra[@]}"
}

echo "==> basic Responses preflight"
if ! run_codex "Reply with exactly: OK" >/tmp/remote-agent-preflight.out 2>/tmp/remote-agent-preflight.err; then
  echo "Basic Codex preflight failed:" >&2
  cat /tmp/remote-agent-preflight.err >&2 || true
  exit 1
fi
if ! grep -qi "OK" /tmp/remote-agent-preflight.out; then
  echo "Basic preflight returned unexpected output:" >&2
  cat /tmp/remote-agent-preflight.out >&2
  exit 1
fi

echo "==> live web-search preflight"
if ! run_codex --search "Use web search if available. Reply with one short word." \
  >/tmp/remote-agent-search-preflight.out 2>/tmp/remote-agent-search-preflight.err; then
  echo "web search preflight failed; caller may disable web_search" >&2
  exit 3
fi

echo "Codex preflight passed"
