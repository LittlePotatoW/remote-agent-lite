# shellcheck shell=bash
# 提交前同步远端主干：检查 RAL_SYNC_REMOTE/RAL_SYNC_BRANCH（默认 origin/main）
# 有没有新提交，有就把主干合并进当前开发分支。
#
# 由 .githooks/pre-commit 在文件检查通过后 source。也可以单独调用：
#   bash -c '. .githooks/hooks-lib.sh; . .githooks/sync-upstream.sh; ral_sync_main'
#
# 为什么不能悄悄合并完继续提交：git 在 pre-commit 阶段已经锁定 HEAD，
# 而且不会把钩子里新写的 MERGE_HEAD 当成这次提交的父提交。所以这里的做法是：
#   1. 把暂存内容做成快照（git write-tree）
#   2. 索引回到 HEAD，工作区文件不动
#   3. 干净地合并主干，并单独生成一个 merge commit
#   4. 把第 1 步的暂存内容叠回索引
#   5. 退出码 1 取消本次提交，提示重新执行一次提交命令
# 这样你的改动不会和合并混在一条提交里，也不会丢。
#
# 可调项（写在 .githooks/config 里，环境变量优先）：
#   RAL_SYNC_ENABLED        1=启用（默认），0=不检查远端
#   RAL_SYNC_REMOTE         远端名，默认 origin
#   RAL_SYNC_BRANCH         远端主干分支名，默认 main
#   RAL_SYNC_TIMEOUT        fetch 超时秒数，默认 25；超时或失败只警告，不拦提交
#   RAL_SYNC_STRATEGY       merge=自动合并（默认）/ check=只提示并拒绝本次提交
#   RAL_SYNC_PRINT_COMMITS  1=打印要带进来的提交（默认）
#   RAL_GIT_PROXY           可选代理，例如 http://127.0.0.1:8899

RAL_SYNC_ENABLED="${RAL_SYNC_ENABLED:-1}"
RAL_SYNC_REMOTE="${RAL_SYNC_REMOTE:-origin}"
RAL_SYNC_BRANCH="${RAL_SYNC_BRANCH:-main}"
RAL_SYNC_TIMEOUT="${RAL_SYNC_TIMEOUT:-25}"
RAL_SYNC_STRATEGY="${RAL_SYNC_STRATEGY:-merge}"
RAL_SYNC_PRINT_COMMITS="${RAL_SYNC_PRINT_COMMITS:-1}"
RAL_GIT_PROXY="${RAL_GIT_PROXY:-}"
RAL_SYNC_FETCH_OUT=""

# 单独调用（没 source hooks-lib.sh）时给个同名的兜底输出函数
if ! command -v ral_stderr >/dev/null 2>&1; then
  ral_stderr() { printf '%s\n' "$*" >&2; }
fi

ral_sync_merge_in_progress() {
  git rev-parse -q --verify MERGE_HEAD >/dev/null 2>&1
}

# 把索引还原成某棵树的快照（不动工作区文件）
ral_sync_restore_index() {
  [ -n "${1:-}" ] || return 1
  git read-tree "$1" >/dev/null 2>&1
}

ral_sync_end_internal() {
  if [ -n "${1:-}" ]; then
    export RAL_HOOKS_INTERNAL="$1"
  else
    unset RAL_HOOKS_INTERNAL
  fi
  unset RAL_SYNC_IN_PROGRESS
}

# fetch 远端主干。返回 0 = 成功；非 0 = 失败/超时，输出放在 RAL_SYNC_FETCH_OUT
ral_sync_fetch() {
  local rc
  if [ -n "$RAL_GIT_PROXY" ]; then
    export http_proxy="$RAL_GIT_PROXY"
    export https_proxy="$RAL_GIT_PROXY"
  fi
  if command -v timeout >/dev/null 2>&1; then
    RAL_SYNC_FETCH_OUT="$(timeout "$RAL_SYNC_TIMEOUT" git fetch --quiet --no-tags "$RAL_SYNC_REMOTE" "$RAL_SYNC_BRANCH" 2>&1)"
  else
    RAL_SYNC_FETCH_OUT="$(git fetch --quiet --no-tags "$RAL_SYNC_REMOTE" "$RAL_SYNC_BRANCH" 2>&1)"
  fi
  rc=$?
  if [ -n "$RAL_GIT_PROXY" ]; then
    unset http_proxy
    unset https_proxy
  fi
  return "$rc"
}

ral_sync_refuse_manually() {
  ral_stderr "  先手动合并再提交："
  ral_stderr "    git merge $RAL_SYNC_REMOTE/$RAL_SYNC_BRANCH"
  ral_stderr "  确需跳过检查：git commit --no-verify"
}

# 主流程：返回 0 = 放行本次提交；1 = 拦下本次提交（可能已经顺手合并了主干）
ral_sync_main() {
  [ "$RAL_SYNC_ENABLED" = "1" ] || return 0
  [ "${RAL_SYNC_IN_PROGRESS:-0}" = "1" ] && return 0
  git rev-parse --git-dir >/dev/null 2>&1 || return 0
  git remote get-url "$RAL_SYNC_REMOTE" >/dev/null 2>&1 || return 0

  if ral_sync_merge_in_progress; then
    ral_stderr "ℹ️  上一次合并还没结束，跳过 $RAL_SYNC_BRANCH 更新检查"
    return 0
  fi

  local branch
  branch="$(git symbolic-ref --short -q HEAD 2>/dev/null || true)"
  if [ -z "$branch" ]; then
    ral_stderr "ℹ️  当前不在分支上（detached HEAD），跳过 $RAL_SYNC_BRANCH 更新检查"
    return 0
  fi
  [ "$branch" = "$RAL_SYNC_BRANCH" ] && return 0

  if ! ral_sync_fetch; then
    ral_stderr ""
    ral_stderr "⚠️  拉取 $RAL_SYNC_REMOTE/$RAL_SYNC_BRANCH 失败，跳过主干检查（不拦本次提交）"
    if [ -n "$RAL_SYNC_FETCH_OUT" ]; then
      printf '%s\n' "$RAL_SYNC_FETCH_OUT" | sed 's/^/    /' >&2
    fi
    ral_stderr "    直连不通时可设 RAL_GIT_PROXY=http://127.0.0.1:<port> 让 fetch 走代理"
    return 0
  fi

  git rev-parse -q --verify FETCH_HEAD >/dev/null 2>&1 || return 0
  git merge-base --is-ancestor FETCH_HEAD HEAD 2>/dev/null && return 0

  local count
  count="$(git rev-list --count HEAD..FETCH_HEAD 2>/dev/null || printf '?')"
  ral_stderr ""
  ral_stderr "🔄 $RAL_SYNC_REMOTE/$RAL_SYNC_BRANCH 比 $branch 多 $count 个提交"
  if [ "$RAL_SYNC_PRINT_COMMITS" = "1" ] || [ "$RAL_SYNC_STRATEGY" != "merge" ]; then
    git log --oneline --no-decorate HEAD..FETCH_HEAD 2>/dev/null | sed 's/^/    /' >&2
  fi

  if [ "$RAL_SYNC_STRATEGY" != "merge" ]; then
    ral_stderr "  RAL_SYNC_STRATEGY=$RAL_SYNC_STRATEGY：先合并主干再提交"
    ral_sync_refuse_manually
    return 1
  fi

  # 主干改过的文件如果正好也在你的暂存区里，就不自动合并，交给人工处理更安全
  local base main_files staged_files overlap
  base="$(git merge-base HEAD FETCH_HEAD 2>/dev/null)"
  main_files="$(git diff --name-only "$base" FETCH_HEAD 2>/dev/null | sort)"
  staged_files="$(git diff --cached --name-only 2>/dev/null | sort)"
  if [ -n "$main_files" ] && [ -n "$staged_files" ]; then
    overlap="$(comm -12 <(printf '%s\n' "$main_files") <(printf '%s\n' "$staged_files") 2>/dev/null | sed '/^$/d' | tr '\n' ' ')"
  fi
  if [ -n "${overlap:-}" ]; then
    ral_stderr "  ⚠️  这些文件主干和你的暂存区都改了：$overlap"
    ral_stderr "     不自动合并，避免把你的改动和主干改动搅在一起。"
    ral_sync_refuse_manually
    return 1
  fi

  local staged_tree head_old saved_internal merge_out merge_rc merge_msg
  staged_tree="$(git write-tree 2>/dev/null || true)"
  if [ -z "$staged_tree" ]; then
    ral_stderr "  ⚠️  暂存区状态异常（git write-tree 失败），跳过自动合并"
    return 0
  fi
  head_old="$(git rev-parse HEAD)"
  saved_internal="${RAL_HOOKS_INTERNAL:-}"
  export RAL_HOOKS_INTERNAL=1
  export RAL_SYNC_IN_PROGRESS=1

  if ! git reset -q --mixed HEAD; then
    ral_sync_restore_index "$staged_tree"
    ral_sync_end_internal "$saved_internal"
    ral_stderr "  ⛔ 索引复位失败，跳过自动合并（你的暂存内容没有变化）"
    return 0
  fi

  merge_out="$(git merge --no-commit --no-ff FETCH_HEAD 2>&1)"
  merge_rc=$?
  if [ "$merge_rc" -ne 0 ]; then
    git merge --abort >/dev/null 2>&1 || true
    ral_sync_restore_index "$staged_tree"
    ral_sync_end_internal "$saved_internal"
    ral_stderr "  ⛔ 自动合并失败（你的暂存内容已还原）："
    printf '%s\n' "$merge_out" | sed 's/^/    /' >&2
    ral_sync_refuse_manually
    return 1
  fi

  # 主干带进来的文件同样要过一遍文件策略
  if command -v ral_scan_staged >/dev/null 2>&1; then
    ral_scan_staged
    if [ "$(ral_violation_count)" -gt 0 ]; then
      git merge --abort >/dev/null 2>&1 || true
      ral_sync_restore_index "$staged_tree"
      ral_sync_end_internal "$saved_internal"
      ral_stderr ""
      ral_stderr "⛔ 主干带进来的文件里有不允许提交的内容，已撤销这次合并"
      ral_print_violations "以下文件不能进仓库："
      return 1
    fi
  fi

  merge_msg="Merge $RAL_SYNC_REMOTE/$RAL_SYNC_BRANCH into $branch (git hook auto sync)"
  if ! git commit -q --no-verify -m "$merge_msg" >/dev/null 2>&1; then
    git merge --abort >/dev/null 2>&1 || true
    ral_sync_restore_index "$staged_tree"
    ral_sync_end_internal "$saved_internal"
    ral_stderr "  ⛔ 生成 merge commit 失败，已撤销这次合并"
    ral_sync_refuse_manually
    return 1
  fi

  if ! git diff-tree -p --binary --full-index "$head_old" "$staged_tree" | git apply --cached --whitespace=nowarn - >/dev/null 2>&1; then
    ral_sync_end_internal "$saved_internal"
    ral_stderr "  ⛔ 已经合并 $RAL_SYNC_REMOTE/$RAL_SYNC_BRANCH，但你的暂存内容没能自动叠回索引"
    ral_stderr "     暂存快照：$staged_tree"
    ral_stderr "     查看：git diff-tree -p $head_old $staged_tree"
    ral_stderr "     请手动 git add 后再提交"
    return 1
  fi
  ral_sync_end_internal "$saved_internal"

  ral_stderr "✅ 已把 $RAL_SYNC_REMOTE/$RAL_SYNC_BRANCH 合并进 $branch（1 个 merge commit）"
  ral_stderr "   本次提交被取消，避免你的改动和 merge commit 混在一条提交里；"
  ral_stderr "   你的暂存内容没有丢，重新执行一次刚才的提交命令即可（这次会直接提交）。"
  return 1
}
