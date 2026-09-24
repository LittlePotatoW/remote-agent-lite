#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
  echo "Please run as root." >&2
  exit 1
fi

systemctl disable --now remote-agent-lite.service remote-agent-codex.service 2>/dev/null || true
rm -f /etc/systemd/system/remote-agent-lite.service /etc/systemd/system/remote-agent-codex.service
rm -f /usr/local/bin/remote-agent-info
systemctl daemon-reload

echo "Services and unit files removed. Application, users, and data are preserved."
if [[ "${1:-}" == "--purge" ]]; then
  echo "Purging application and data directories..."
  rm -rf /opt/remote-agent-lite /var/lib/remote-agent-lite /srv/remote-agent-lite
  echo "Data directories removed. Users were left in place for safety."
fi

