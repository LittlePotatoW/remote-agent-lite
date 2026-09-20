#!/usr/bin/env bash
set -euo pipefail

BASE="${RAL_SMOKE_BASE:-http://127.0.0.1:8080}"
SECRETS="${RAL_SMOKE_SECRETS:-/root/ral-secrets.env}"
TIMEOUT_SECONDS="${RAL_SMOKE_TIMEOUT:-600}"

if [[ ! -f "${SECRETS}" ]]; then
  echo "secrets file not found: ${SECRETS}" >&2
  exit 2
fi

set -a
# shellcheck disable=SC1090
. "${SECRETS}"
set +a

if [[ -z "${RAL_INSTALL_ADMIN_PASSWORD:-}" ]]; then
  echo "RAL_INSTALL_ADMIN_PASSWORD is missing" >&2
  exit 2
fi

COOKIE="$(mktemp)"
INPUT_FILE="$(mktemp)"
OUTPUT_FILE="$(mktemp)"
trap 'rm -f "${COOKIE}" "${INPUT_FILE}" "${OUTPUT_FILE}"' EXIT

json_value() {
  python3 -c "import json,sys; print(json.load(sys.stdin)${1})"
}

echo "==> health"
curl -fsS "${BASE}/api/healthz" >/dev/null

echo "==> login"
curl -fsS -c "${COOKIE}" \
  -H 'Content-Type: application/json' \
  -d "{\"password\":\"${RAL_INSTALL_ADMIN_PASSWORD}\"}" \
  "${BASE}/api/auth/login" >/dev/null

echo "==> create project and session"
PROJECT_ID="$(
  curl -fsS -b "${COOKIE}" -H 'Content-Type: application/json' \
    -d '{"name":"ral-smoke"}' "${BASE}/api/projects" \
    | json_value '["project"]["id"]'
)"
SESSION_ID="$(
  curl -fsS -b "${COOKIE}" -H 'Content-Type: application/json' \
    -d '{}' "${BASE}/api/projects/${PROJECT_ID}/sessions" \
    | json_value '["session"]["id"]'
)"

echo "==> upload a small file"
printf 'hello from remote-agent-lite smoke test\n' > "${INPUT_FILE}"
SIZE="$(stat -c '%s' "${INPUT_FILE}")"
UPLOAD_ID="$(
  curl -fsS -b "${COOKIE}" -H 'Content-Type: application/json' \
    -d "{\"filename\":\"smoke-input.txt\",\"size\":${SIZE}}" \
    "${BASE}/api/projects/${PROJECT_ID}/uploads/init" \
    | json_value '["upload_id"]'
)"
curl -fsS -b "${COOKIE}" -X PUT \
  -H 'Content-Type: application/octet-stream' \
  --data-binary "@${INPUT_FILE}" \
  "${BASE}/api/uploads/${UPLOAD_ID}/parts/0" >/dev/null
curl -fsS -b "${COOKIE}" -H 'Content-Type: application/json' \
  -d '{}' "${BASE}/api/uploads/${UPLOAD_ID}/complete" >/dev/null

echo "==> send a Codex turn"
curl -fsS -b "${COOKIE}" -H 'Content-Type: application/json' \
  -d '{"prompt":"Create smoke-result.txt in the project root containing exactly SMOKE_OK. Then reply only SMOKE_OK."}' \
  "${BASE}/api/sessions/${SESSION_ID}/turns" >/dev/null

DEADLINE=$((SECONDS + TIMEOUT_SECONDS))
MESSAGE_JSON=""
ASSISTANT_STATUS=""
while (( SECONDS < DEADLINE )); do
  MESSAGE_JSON="$(
    curl -fsS -b "${COOKIE}" \
      "${BASE}/api/sessions/${SESSION_ID}/messages?limit=50"
  )"
  ASSISTANT_STATUS="$(
    printf '%s' "${MESSAGE_JSON}" \
      | python3 -c 'import json,sys; d=json.load(sys.stdin); items=[m for m in d["messages"] if m["role"]=="assistant"]; print(items[-1]["status"] if items else "none")'
  )"
  case "${ASSISTANT_STATUS}" in
    completed) break ;;
    failed) echo "Codex turn failed"; printf '%s\n' "${MESSAGE_JSON}"; exit 1 ;;
  esac
  sleep 2
done

if [[ "${ASSISTANT_STATUS}" != "completed" ]]; then
  echo "Codex turn timed out (status=${ASSISTANT_STATUS})" >&2
  exit 1
fi

ASSISTANT_TEXT="$(
  printf '%s' "${MESSAGE_JSON}" \
    | python3 -c 'import json,sys; d=json.load(sys.stdin); items=[m for m in d["messages"] if m["role"]=="assistant"]; print(items[-1]["content"] if items else "")'
)"
echo "assistant: ${ASSISTANT_TEXT}"

echo "==> download generated file"
curl -fsS -b "${COOKIE}" \
  -o "${OUTPUT_FILE}" \
  "${BASE}/api/projects/${PROJECT_ID}/files/download?path=smoke-result.txt"
grep -qx 'SMOKE_OK' "${OUTPUT_FILE}"

echo "==> cleanup"
curl -fsS -b "${COOKIE}" -X DELETE "${BASE}/api/projects/${PROJECT_ID}" >/dev/null

echo "==> service status"
systemctl is-active remote-agent-codex.service
systemctl is-active remote-agent-lite.service
remote-agent-info --json | python3 -c 'import json,sys; d=json.load(sys.stdin); print("memory_available_mb="+str(d["memory"]["available_mb"])); print("disk_free_mb="+str(d["disk"]["free_mb"]))'

echo "SMOKE_TEST_PASSED"
