# shellcheck shell=bash
# .githooks 共用检测逻辑，由 pre-commit / post-index-change source。
# 只依赖 git + POSIX 工具；请用 bash 3.2+ 执行（macOS 自带 bash 也可）。

RAL_HOOKS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
RAL_CONFIG_FILE="${RAL_CONFIG_FILE:-$RAL_HOOKS_DIR/config}"
if [ -r "$RAL_CONFIG_FILE" ]; then
  # shellcheck disable=SC1090
  . "$RAL_CONFIG_FILE"
fi

# 环境变量覆盖标量配置
RAL_MAX_BYTES="${RAL_MAX_BYTES:-1048576}"
RAL_BLOCK_SYMLINKS="${RAL_BLOCK_SYMLINKS:-1}"
RAL_BLOCK_GITLINKS="${RAL_BLOCK_GITLINKS:-1}"
RAL_BLOCK_IGNORED="${RAL_BLOCK_IGNORED:-1}"
RAL_REQUIRE_UTF8="${RAL_REQUIRE_UTF8:-1}"
RAL_AUTO_UNSTAGE="${RAL_AUTO_UNSTAGE:-1}"
RAL_SAMPLE_BYTES="${RAL_SAMPLE_BYTES:-8192}"
RAL_ALLOWLIST_FILE="${RAL_ALLOWLIST_FILE:-.githooks/allowlist.txt}"

# 违规记录：类型 / 路径 / 细节（三个平行数组，避免解析分隔符）
RAL_V_TYPE=()
RAL_V_PATH=()
RAL_V_DETAIL=()
RAL_STAGED=()
# 空数组在 bash 3.2 + set -u 下会报错，这里放一个永不匹配的哨兵值
RAL_ALLOW=("!__no_allowlist_entry__!")
RAL_SKIPPED=()

ral_stderr() {
  printf '%s\n' "$*" >&2
}

ral_repo_root() {
  git rev-parse --show-toplevel 2>/dev/null || pwd
}

# 收集暂存区里新增/修改/复制的文件（重命名只取新名字）
ral_staged_files() {
  local entry
  RAL_STAGED=()
  while IFS= read -r -d '' entry; do
    [ -n "$entry" ] && RAL_STAGED+=("$entry")
  done < <(git -c core.quotePath=false diff --cached --name-only -z --diff-filter=ACMR 2>/dev/null)
  return 0
}

ral_allowlist_patterns() {
  RAL_ALLOW=("!__no_allowlist_entry__!")
  local file="$RAL_ALLOWLIST_FILE" line
  [ -r "$file" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    line="${line%%#*}"
    line="$(printf '%s' "$line" | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
    [ -n "$line" ] && RAL_ALLOW+=("$line")
  done < "$file"
  return 0
}

# 命中任意 pattern：匹配完整路径、basename，以及路径中的每一段
ral_matches_any() {
  local path="$1"
  shift
  local base="${path##*/}" segment pattern
  [ "$#" -gt 0 ] || return 1
  for pattern in "$@"; do
    [ -n "$pattern" ] || continue
    case "$path" in $pattern) return 0 ;; esac
    case "$base" in $pattern) return 0 ;; esac
    local rest="$path"
    while [ -n "$rest" ]; do
      segment="${rest%%/*}"
      if [ "$rest" = "$segment" ]; then rest=""; else rest="${rest#*/}"; fi
      [ -n "$segment" ] || continue
      case "$segment" in $pattern) return 0 ;; esac
    done
  done
  return 1
}

# 索引里的文件模式：100644 普通文件 / 100755 可执行 / 120000 符号链接 / 160000 submodule
ral_index_mode() {
  local line
  line="$(git ls-files -s -z -- "$1" 2>/dev/null | tr '\0' '\n' | head -n 1)"
  printf '%s' "${line%% *}"
}

# 索引中该文件的大小；git 读不到时退回工作区文件
ral_blob_size() {
  local size
  size="$(git cat-file -s ":$1" 2>/dev/null || true)"
  if [ -z "$size" ]; then
    size="$(wc -c < "$1" 2>/dev/null | tr -d ' ' || true)"
  fi
  printf '%s' "${size:-0}"
}

ral_blob_has_nul() {
  [ -n "${1:-}" ] || return 1
  local total stripped
  total="$(git cat-file blob ":$1" 2>/dev/null | head -c "$RAL_SAMPLE_BYTES" | wc -c | tr -d ' ')"
  stripped="$(git cat-file blob ":$1" 2>/dev/null | head -c "$RAL_SAMPLE_BYTES" | tr -d '\000' | wc -c | tr -d ' ')"
  [ "${total:-0}" -gt "${stripped:-0}" ]
}

ral_utf8_checker() {
  if command -v iconv >/dev/null 2>&1; then
    printf 'iconv'
  elif command -v python3 >/dev/null 2>&1; then
    printf 'python3'
  fi
}

# 整份内容做 UTF-8 校验，避免按字节截断把多字节字符切一半造成误报
ral_blob_is_utf8() {
  [ -n "${1:-}" ] || return 0
  local checker
  checker="$(ral_utf8_checker)"
  case "$checker" in
    iconv)
      git cat-file blob ":$1" 2>/dev/null | iconv -f UTF-8 -t UTF-8 >/dev/null 2>&1
      ;;
    python3)
      git cat-file blob ":$1" 2>/dev/null | python3 -c 'import sys
data = sys.stdin.buffer.read()
try:
    data.decode("utf-8")
except UnicodeDecodeError:
    sys.exit(1)
sys.exit(0)'
      ;;
    *)
      return 0
      ;;
  esac
}

ral_add_violation() {
  RAL_V_TYPE+=("$1")
  RAL_V_PATH+=("$2")
  RAL_V_DETAIL+=("$3")
}

ral_human_bytes() {
  awk -v bytes="$1" 'BEGIN {
    if (bytes >= 1048576) printf "%.1f MB", bytes / 1048576;
    else if (bytes >= 1024) printf "%.1f KB", bytes / 1024;
    else printf "%d B", bytes;
  }'
}

# 单个文件检查，命中就写入 RAL_V_* 数组
ral_check_path() {
  local path="$1" mode size

  if ral_matches_any "$path" "${RAL_ALLOW[@]}"; then
    RAL_SKIPPED+=("$path")
    return 0
  fi

  mode="$(ral_index_mode "$path")"
  if [ "$mode" = "120000" ]; then
    [ "$RAL_BLOCK_SYMLINKS" = "1" ] &&
      ral_add_violation "symlink" "$path" "符号链接，仓库里只接受普通文件"
    return 0
  fi
  if [ "$mode" = "160000" ]; then
    [ "$RAL_BLOCK_GITLINKS" = "1" ] &&
      ral_add_violation "submodule" "$path" "submodule 引用，请改用依赖清单"
    return 0
  fi

  # 先看内容本身：二进制 / 超大 / 非 UTF-8，这样报错信息最贴近真实原因
  if ral_blob_has_nul "$path"; then
    ral_add_violation "binary" "$path" "二进制内容（前 $(ral_human_bytes "$RAL_SAMPLE_BYTES") 内出现 NUL 字节）"
    return 0
  fi

  size="$(ral_blob_size "$path")"
  case "$size" in
    '' | *[!0-9]*) size=0 ;;
  esac
  if [ "$size" -gt "$RAL_MAX_BYTES" ]; then
    ral_add_violation "large" "$path" "$(ral_human_bytes "$size")，超过单文件上限 $(ral_human_bytes "$RAL_MAX_BYTES")"
    return 0
  fi

  if [ "$RAL_REQUIRE_UTF8" = "1" ] && ! ral_blob_is_utf8 "$path"; then
    ral_add_violation "non-text" "$path" "不是合法 UTF-8 文本"
    return 0
  fi

  # 再看路径规则
  if ral_matches_any "$path" "${RAL_DENY_PATTERNS[@]}"; then
    ral_add_violation "denylist" "$path" "命中禁止提交清单（机密 / 依赖 / 构建产物）"
    return 0
  fi

  if [ "$RAL_BLOCK_IGNORED" = "1" ] && git check-ignore -q --no-index -- "$path" 2>/dev/null; then
    ral_add_violation "ignored" "$path" "被 .gitignore 忽略，是用 git add -f 强行加入的"
    return 0
  fi

  return 0
}

ral_scan_staged() {
  RAL_V_TYPE=()
  RAL_V_PATH=()
  RAL_V_DETAIL=()
  RAL_SKIPPED=()
  ral_allowlist_patterns
  ral_staged_files
  local path
  for path in "${RAL_STAGED[@]:-}"; do
    [ -n "$path" ] && ral_check_path "$path"
  done
  return 0
}

ral_violation_count() {
  printf '%s' "${#RAL_V_PATH[@]}"
}

# 打印违规清单，$1 = 标题
ral_print_violations() {
  local title="$1" index=0 total="${#RAL_V_PATH[@]}"
  [ "$total" -gt 0 ] || return 0
  ral_stderr ""
  ral_stderr "$title"
  while [ "$index" -lt "$total" ]; do
    ral_stderr "  ✗ [${RAL_V_TYPE[$index]}] ${RAL_V_PATH[$index]} — ${RAL_V_DETAIL[$index]}"
    index=$((index + 1))
  done
}

ral_print_footer() {
  ral_stderr ""
  ral_stderr "  取消暂存：git restore --staged -- <路径>（新文件用 git rm --cached -- <路径>）"
  ral_stderr "  确认要提交：把路径写进 .githooks/allowlist.txt（会进入代码评审），或临时放宽"
  ral_stderr "  RAL_MAX_BYTES / RAL_BLOCK_SYMLINKS 等配置见 .githooks/config"
}

# 把单个路径从索引里撤出，兼容「还没有任何提交」和「文件本就已在 HEAD 里」两种情况
ral_unstage_path() {
  local path="$1"
  export RAL_HOOKS_INTERNAL=1
  if git rev-parse -q --verify HEAD >/dev/null 2>&1 &&
    git cat-file -e "HEAD:$path" 2>/dev/null; then
    git restore --staged -q -- "$path" 2>/dev/null && return 0
  fi
  git rm --cached -q -- "$path" >/dev/null 2>&1 && return 0
  return 1
}
