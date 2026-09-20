#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run as root." >&2
  exit 1
fi

if [[ ! -r /etc/os-release ]]; then
  echo "Cannot detect the operating system." >&2
  exit 2
fi
# shellcheck disable=SC1091
source /etc/os-release
if [[ "${ID:-}" != "ubuntu" ]]; then
  echo "This installer targets Ubuntu 24.04 (detected ${ID:-unknown})." >&2
  exit 2
fi

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
APP_DIR="/opt/remote-agent-lite/app"
CODEX_DIR="/opt/remote-agent-lite/codex"
DATA_ROOT="/srv/remote-agent-lite"
STATE_DIR="/var/lib/remote-agent-lite"
ETC_DIR="/etc/remote-agent-lite"
CODEX_VERSION="${RAL_CODEX_VERSION:-0.146.1}"
WEB_SEARCH_MODE="${RAL_INSTALL_WEB_SEARCH:-live}"

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends \
  ca-certificates curl nodejs npm python3 python3-pip python3-venv rsync sudo procps openssl sqlite3

if ! swapon --show 2>/dev/null | grep -q .; then
  if [[ ! -f /swapfile ]]; then
    echo "==> creating 2G swap file"
    fallocate -l 2G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
  fi
  swapon /swapfile
  if ! grep -q '^/swapfile[[:space:]]' /etc/fstab; then
    echo '/swapfile none swap sw 0 0' >> /etc/fstab
  fi
fi

groupadd -f remoteagent
if ! id -u remoteagent-web >/dev/null 2>&1; then
  useradd --system --gid remoteagent --home-dir "${STATE_DIR}/web-home" \
    --create-home --shell /usr/sbin/nologin remoteagent-web
fi
if ! id -u remoteagent-codex >/dev/null 2>&1; then
  useradd --system --gid remoteagent --home-dir "${STATE_DIR}/codex-home" \
    --create-home --shell /bin/bash remoteagent-codex
fi
usermod -aG remoteagent remoteagent-web
usermod -aG remoteagent remoteagent-codex

install -d -m 0755 /opt/remote-agent-lite "$APP_DIR" "$CODEX_DIR"
install -d -m 2775 -o remoteagent-web -g remoteagent "$DATA_ROOT" "$DATA_ROOT/projects"
install -d -m 2775 -o remoteagent-web -g remoteagent "$STATE_DIR" "$STATE_DIR/uploads"
install -d -m 0750 -o remoteagent-codex -g remoteagent "$STATE_DIR/codex-home"
install -d -m 0750 -o root -g remoteagent "$ETC_DIR"

echo "==> copying application"
rsync -a --delete \
  --exclude .git \
  --exclude var \
  --exclude .venv \
  --exclude frontend/dist \
  --exclude frontend/node_modules \
  "${REPO_DIR}/" "${APP_DIR}/"

echo "==> installing Python environment"
python3 -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/pip" install --upgrade pip wheel
"${APP_DIR}/.venv/bin/pip" install --no-cache-dir "${APP_DIR}"

echo "==> building web UI"
(
  cd "${APP_DIR}/frontend"
  if [[ -f package-lock.json ]]; then
    npm ci --no-audit --no-fund
  else
    npm install --no-audit --no-fund
  fi
  npm run build
  rm -rf node_modules
)

echo "==> installing Codex ${CODEX_VERSION}"
npm install --global --prefix "${CODEX_DIR}" "@openai/codex@${CODEX_VERSION}"

if [[ -n "${RAL_INSTALL_BASE_URL:-}" ]]; then
  DSAPI_BASE_URL="${RAL_INSTALL_BASE_URL}"
else
  read -r -p "DSAPI Base URL: " DSAPI_BASE_URL
fi
if [[ -n "${RAL_INSTALL_MODEL:-}" ]]; then
  DSAPI_MODEL="${RAL_INSTALL_MODEL}"
else
  read -r -p "DSAPI model name: " DSAPI_MODEL
fi
if [[ -n "${RAL_INSTALL_API_KEY:-}" ]]; then
  DSAPI_API_KEY="${RAL_INSTALL_API_KEY}"
else
  read -r -s -p "DSAPI API key: " DSAPI_API_KEY
  echo
fi
if [[ -z "${DSAPI_BASE_URL}" || -z "${DSAPI_MODEL}" || -z "${DSAPI_API_KEY}" ]]; then
  echo "Base URL, model, and API key are required." >&2
  exit 3
fi
if [[ "${DSAPI_BASE_URL}" == *$'\n'* || "${DSAPI_MODEL}" == *$'\n'* || "${DSAPI_API_KEY}" == *$'\n'* ]]; then
  echo "Configuration values must not contain newlines." >&2
  exit 3
fi

if [[ -n "${RAL_INSTALL_ADMIN_PASSWORD:-}" ]]; then
  ADMIN_PASSWORD="${RAL_INSTALL_ADMIN_PASSWORD}"
else
  read -r -s -p "Admin password (min 8 chars): " ADMIN_PASSWORD
  echo
  read -r -s -p "Repeat admin password: " ADMIN_PASSWORD_2
  echo
  if [[ "${ADMIN_PASSWORD}" != "${ADMIN_PASSWORD_2}" ]]; then
    echo "Passwords do not match." >&2
    exit 3
  fi
fi
if [[ ${#ADMIN_PASSWORD} -lt 8 ]]; then
  echo "Admin password must be at least 8 characters." >&2
  exit 3
fi

echo "==> writing configuration"
cat > "${ETC_DIR}/codex-config.toml" <<EOF
model = "${DSAPI_MODEL}"
model_provider = "dsapi"
approval_policy = "never"
sandbox_mode = "danger-full-access"
web_search = "${WEB_SEARCH_MODE}"

[model_providers.dsapi]
name = "DSAPI"
base_url = "${DSAPI_BASE_URL}"
env_key = "DSAPI_API_KEY"
wire_api = "responses"

[sandbox_workspace_write]
network_access = true
EOF
chmod 0640 "${ETC_DIR}/codex-config.toml"
chown root:remoteagent "${ETC_DIR}/codex-config.toml"
install -m 0600 -o remoteagent-codex -g remoteagent \
  "${ETC_DIR}/codex-config.toml" "${STATE_DIR}/codex-home/config.toml"

cat > "${ETC_DIR}/remote-agent-lite.env" <<EOF
RAL_DATA_DIR=${STATE_DIR}
RAL_PROJECTS_DIR=${DATA_ROOT}/projects
RAL_UPLOADS_DIR=${STATE_DIR}/uploads
RAL_DB_PATH=${STATE_DIR}/remote-agent-lite.db
RAL_CODEX_HOME=${STATE_DIR}/codex-home
RAL_FRONTEND_DIST=${APP_DIR}/frontend/dist
RAL_HOST=0.0.0.0
RAL_PORT=8080
RAL_COOKIE_NAME=ral_session
RAL_SESSION_TTL_DAYS=30
RAL_LOGIN_WINDOW_SECONDS=900
RAL_LOGIN_MAX_FAILURES=5
RAL_LOGIN_LOCK_SECONDS=900
RAL_CODEX_WS_URL=ws://127.0.0.1:4517
RAL_CODEX_TOKEN_FILE=${ETC_DIR}/codex-ws-token
RAL_CODEX_MODEL=${DSAPI_MODEL}
RAL_CODEX_MODEL_PROVIDER=dsapi
RAL_CODEX_WEB_SEARCH=${WEB_SEARCH_MODE}
RAL_CODEX_TURN_TIMEOUT_SECONDS=7200
RAL_UPLOAD_CHUNK_SIZE=5242880
RAL_MAX_FILE_SIZE=209715200
RAL_PROJECT_QUOTA=10737418240
RAL_UPLOAD_TTL_HOURS=24
RAL_DISK_LOW_WATERMARK=5368709120
RAL_DISK_CRITICAL=1073741824
RAL_SSE_COALESCE_MS=100
DSAPI_API_KEY=${DSAPI_API_KEY}
EOF
chmod 0640 "${ETC_DIR}/remote-agent-lite.env"
chown root:remoteagent "${ETC_DIR}/remote-agent-lite.env"

openssl rand -hex 32 > "${ETC_DIR}/codex-ws-token"
chmod 0640 "${ETC_DIR}/codex-ws-token"
chown root:remoteagent "${ETC_DIR}/codex-ws-token"

echo "==> initializing Codex context and password"
sudo -u remoteagent-codex env \
  RAL_DATA_DIR="${STATE_DIR}" \
  RAL_CODEX_HOME="${STATE_DIR}/codex-home" \
  HOME="${STATE_DIR}/codex-home" \
  "${APP_DIR}/.venv/bin/python" -m remote_agent_lite.cli init-context >/dev/null

printf '%s' "${ADMIN_PASSWORD}" | sudo -u remoteagent-web env \
  RAL_DATA_DIR="${STATE_DIR}" \
  RAL_PROJECTS_DIR="${DATA_ROOT}/projects" \
  RAL_UPLOADS_DIR="${STATE_DIR}/uploads" \
  RAL_DB_PATH="${STATE_DIR}/remote-agent-lite.db" \
  "${APP_DIR}/.venv/bin/remote-agent-lite" set-password --password-stdin >/dev/null

install -m 0755 "${REPO_DIR}/deploy/remote-agent-info" /usr/local/bin/remote-agent-info
install -m 0644 "${REPO_DIR}/deploy/systemd/remote-agent-codex.service" /etc/systemd/system/
install -m 0644 "${REPO_DIR}/deploy/systemd/remote-agent-lite.service" /etc/systemd/system/

echo "==> running Codex preflight"
set +e
CODEX_BIN="${CODEX_DIR}/bin/codex" \
  CODEX_HOME_DIR="${STATE_DIR}/codex-home" \
  CODEX_RUN_USER=remoteagent-codex \
  WORKDIR="${DATA_ROOT}" \
  DSAPI_API_KEY="${DSAPI_API_KEY}" \
  WEB_SEARCH_MODE="${WEB_SEARCH_MODE}" \
  bash "${REPO_DIR}/deploy/preflight-codex.sh"
PREFLIGHT_STATUS=$?
set -e
if [[ ${PREFLIGHT_STATUS} -eq 3 ]]; then
  echo "Web search is unavailable; disabling it for this deployment."
  sed -i 's/^web_search = "live"/web_search = "disabled"/' "${ETC_DIR}/codex-config.toml"
  sed -i 's/^web_search = "live"/web_search = "disabled"/' "${STATE_DIR}/codex-home/config.toml"
  sed -i 's/^RAL_CODEX_WEB_SEARCH=.*/RAL_CODEX_WEB_SEARCH=disabled/' "${ETC_DIR}/remote-agent-lite.env"
elif [[ ${PREFLIGHT_STATUS} -ne 0 ]]; then
  echo "Codex preflight failed. Fix the DSAPI configuration and rerun deploy/install.sh." >&2
  exit "${PREFLIGHT_STATUS}"
fi

echo "==> starting services"
systemctl daemon-reload
systemctl enable --now remote-agent-codex.service
systemctl enable --now remote-agent-lite.service
sleep 2
systemctl --no-pager --full status remote-agent-lite.service || true

IP_ADDRESS="$(hostname -I | awk '{print $1}')"
echo
echo "remote-agent-lite is installed."
echo "Open: http://${IP_ADDRESS}:8080"
echo "Remember to open TCP 8080 in the Aliyun security group."
echo "Logs: journalctl -u remote-agent-lite -u remote-agent-codex -f"
