#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run as root." >&2
  exit 1
fi

VERSION="${1:-}"
if [[ ! "${VERSION}" =~ ^[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  echo "Usage: $0 <codex-version>" >&2
  exit 2
fi

CODEX_DIR="/opt/remote-agent-lite/codex"
STATE_DIR="/var/lib/remote-agent-lite"
ETC_DIR="/etc/remote-agent-lite"
APP_DIR="/opt/remote-agent-lite/app"

echo "==> backing up configuration and database"
install -d -m 0700 /var/backups/remote-agent-lite
cp -a "${ETC_DIR}/remote-agent-lite.env" "/var/backups/remote-agent-lite/env.$(date +%s)"
DSAPI_API_KEY="$(sed -n 's/^DSAPI_API_KEY=//p' "${ETC_DIR}/remote-agent-lite.env" | head -n 1)"
if [[ -f "${STATE_DIR}/remote-agent-lite.db" ]]; then
  sqlite3 "${STATE_DIR}/remote-agent-lite.db" ".backup '/var/backups/remote-agent-lite/db.$(date +%s).sqlite'"
fi

echo "==> installing Codex ${VERSION}"
if [[ -d "${CODEX_DIR}.previous" ]]; then
  rm -rf "${CODEX_DIR}.previous"
fi
mv "${CODEX_DIR}" "${CODEX_DIR}.previous"
npm install --global --prefix "${CODEX_DIR}" "@openai/codex@${VERSION}"

set +e
CODEX_BIN="${CODEX_DIR}/bin/codex" \
  CODEX_HOME_DIR="${STATE_DIR}/codex-home" \
  CODEX_RUN_USER=remoteagent-codex \
  WORKDIR=/srv/remote-agent-lite \
  DSAPI_API_KEY="${DSAPI_API_KEY}" \
  bash "${APP_DIR}/deploy/preflight-codex.sh"
STATUS=$?
set -e

if [[ ${STATUS} -ne 0 && ${STATUS} -ne 3 ]]; then
  echo "Preflight failed; rolling back." >&2
  rm -rf "${CODEX_DIR}"
  mv "${CODEX_DIR}.previous" "${CODEX_DIR}"
  systemctl restart remote-agent-codex.service remote-agent-lite.service
  exit "${STATUS}"
fi

systemctl restart remote-agent-codex.service remote-agent-lite.service
echo "Upgrade to Codex ${VERSION} complete. Previous version kept at ${CODEX_DIR}.previous"
