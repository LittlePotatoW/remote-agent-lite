#!/usr/bin/env bash
# 在本地仓库安装 .githooks（写入 core.hooksPath，不改全局 git 配置）。
#
#   bash .githooks/install.sh              # 安装 / 重新安装
#   bash .githooks/install.sh --check      # 只看当前状态
#   bash .githooks/install.sh --uninstall  # 卸载（恢复默认 .git/hooks）
set -euo pipefail

HOOKS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(git -C "$HOOKS_DIR" rev-parse --show-toplevel)"
MODE="${1:-install}"

current="$(git -C "$REPO_ROOT" config --local --get core.hooksPath || true)"

case "$MODE" in
  --check | check)
    if [ "$current" = ".githooks" ]; then
      echo "已安装：core.hooksPath=$current（$REPO_ROOT）"
    else
      echo "未安装：core.hooksPath=${current:-<未设置，使用 .git/hooks>}"
      exit 1
    fi
    ;;
  --uninstall | uninstall)
    git -C "$REPO_ROOT" config --local --unset core.hooksPath 2>/dev/null || true
    echo "已卸载：core.hooksPath 已恢复默认，.githooks 目录保留"
    ;;
  *)
    chmod +x "$HOOKS_DIR"/pre-commit "$HOOKS_DIR"/post-index-change 2>/dev/null || true
    git -C "$REPO_ROOT" config --local core.hooksPath .githooks
    echo "已安装 git hook：core.hooksPath=.githooks（$REPO_ROOT）"
    echo "验证：bash .githooks/run-tests.sh"
    ;;
esac
