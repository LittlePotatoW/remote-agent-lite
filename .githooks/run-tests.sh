#!/usr/bin/env bash
# .githooks 自测：在临时仓库里逐条验证拦截规则与 allowlist 例外。
#   bash .githooks/run-tests.sh          # 跑完自动清理
#   bash .githooks/run-tests.sh --keep   # 保留临时仓库便于排查
set -uo pipefail

HOOKS_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
KEEP="${1:-}"

TMP_ROOT="${TMPDIR:-/tmp}"
WORK="$(mktemp -d "$TMP_ROOT/ral-hooks-test.XXXXXX")"
REPO="$WORK/repo"
PASS=0
FAIL=0

cleanup() {
  if [ "$KEEP" = "--keep" ]; then
    echo "保留临时仓库：$REPO"
    return
  fi
  rm -rf "$WORK" 2>/dev/null || python3 -c 'import shutil,sys; shutil.rmtree(sys.argv[1], ignore_errors=True)' "$WORK"
}
trap cleanup EXIT

ok() {
  PASS=$((PASS + 1))
  printf '  \033[32mPASS\033[0m %s\n' "$1"
}

bad() {
  FAIL=$((FAIL + 1))
  printf '  \033[31mFAIL\033[0m %s\n' "$1"
  [ -n "${2:-}" ] && printf '       %s\n' "$2"
}

mkdir -p "$REPO"
git -C "$REPO" init -q .
git -C "$REPO" config user.email hooks@test.local
git -C "$REPO" config user.name "hook test"
cp -R "$HOOKS_DIR" "$REPO/.githooks"
bash "$REPO/.githooks/install.sh" >/dev/null

# 临时仓库自己的忽略规则，用来验证「git add -f 强行加入被忽略文件」
printf '.env\n*.log\n.venv/\nlocal-only/\n' > "$REPO/.gitignore"
mkdir -p "$REPO/local-only"
printf 'note\n' > "$REPO/local-only/note.md"
mkdir -p "$REPO/deploy"

printf 'hello\n' > "$REPO/good.txt"
printf 'hello\n' > "$REPO/.env.example"
printf '\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR' > "$REPO/logo.png"
printf '\x00\x01\x02binary' > "$REPO/blob.dat"
printf 'name=app\n' > "$REPO/.env"
printf -- '-----BEGIN PRIVATE KEY-----\n' > "$REPO/deploy.key"
printf 'log line\n' > "$REPO/app.log"
printf '\xff\xfeh\x00i\x00' > "$REPO/utf16.txt"
printf 'caf\xe9 latin-1\n' > "$REPO/latin1.txt"
ln -s /etc/hostname "$REPO/link.txt"
head -c 2097152 /dev/zero | tr '\0' 'a' > "$REPO/big.txt"
printf 'x\n' > "$REPO/big.csv"

stage() { RAL_AUTO_UNSTAGE=0 git -C "$REPO" add -- "$1" 2>/dev/null; }
stage_forced() { RAL_AUTO_UNSTAGE=0 git -C "$REPO" add -f -- "$1" 2>/dev/null; }
unstage_all() { git -C "$REPO" reset -q 2>/dev/null; }
run_pre_commit() { (cd "$REPO" && RAL_AUTO_UNSTAGE=0 ./.githooks/pre-commit 2>&1); }

expect_blocked() { # $1 描述  $2 文件  $3 期望出现的关键字  $4=force 时用 git add -f
  local desc="$1" file="$2" needle="$3" out rc
  unstage_all
  if [ "${4:-}" = "force" ]; then stage_forced "$file"; else stage "$file"; fi
  out="$(run_pre_commit)"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    bad "$desc" "pre-commit 放行了，输出：${out:-<空>}"
  elif ! printf '%s' "$out" | grep -q "$needle"; then
    bad "$desc" "被拦下但提示里没有 '$needle'：$out"
  else
    ok "$desc"
  fi
  unstage_all
}

expect_allowed() { # $1 描述  $2 文件
  local desc="$1" file="$2" out rc
  unstage_all
  stage "$file"
  out="$(run_pre_commit)"
  rc=$?
  if [ "$rc" -eq 0 ]; then
    ok "$desc"
  else
    bad "$desc" "被误拦：$out"
  fi
  unstage_all
}

echo "== 拦截规则 =="
expect_allowed "普通文本文件放行" "good.txt"
expect_allowed ".env.example 模板放行（allowlist）" ".env.example"
expect_blocked "PNG 二进制被拦" "logo.png" "binary"
expect_blocked ".dat 二进制被拦" "blob.dat" "binary"
expect_blocked "UTF-16 文本被拦" "utf16.txt" "binary"
expect_blocked "非 UTF-8 文本被拦" "latin1.txt" "non-text"
expect_blocked "2 MiB 大文件被拦" "big.txt" "large"
expect_blocked ".env 被 git add -f 强加后被拦" ".env" "denylist" force
expect_blocked "私钥 .key 被拦" "deploy.key" "denylist"
expect_blocked ".log 被拦（黑名单）" "app.log" "denylist" force
expect_blocked "被忽略的目录内容被拦" "local-only/note.md" "ignored" force
expect_blocked "符号链接被拦" "link.txt" "symlink"

echo "== 暂存区自动撤销 =="
unstage_all
git -C "$REPO" add -- logo.png >/dev/null 2>&1
if [ -z "$(git -C "$REPO" diff --cached --name-only)" ]; then
  ok "git add 违规文件后已被自动移出暂存区"
else
  bad "git add 违规文件后仍在暂存区" "$(git -C "$REPO" diff --cached --name-only | tr '\n' ' ')"
fi
unstage_all
git -C "$REPO" add -- good.txt >/dev/null 2>&1
if [ "$(git -C "$REPO" diff --cached --name-only)" = "good.txt" ]; then
  ok "git add 正常文件保持暂存"
else
  bad "git add 正常文件被误撤销"
fi

echo "== allowlist 例外 =="
unstage_all
printf 'logo.png\nbig.txt\n' >> "$REPO/.githooks/allowlist.txt"
expect_allowed "allowlist 中的 PNG 放行" "logo.png"
expect_allowed "allowlist 中的大文件放行" "big.txt"

echo "== 正常提交 =="
unstage_all
git -C "$REPO" add -- good.txt >/dev/null 2>&1
if git -C "$REPO" commit -qm "test commit" 2>/dev/null; then
  ok "只有合法文件时 git commit 成功"
else
  bad "合法提交被拦"
fi
if git -C "$REPO" log -1 --format=%s | grep -q "test commit"; then
  ok "提交内容正确"
else
  bad "提交没有落在 HEAD 上"
fi

echo
echo
echo "== 提交前同步远端主干 =="
SYNC_ORIGIN="$WORK/origin.git"
SYNC_UP="$WORK/sync-upstream"
SYNC_DEV="$WORK/sync-dev"
git init -q --bare "$SYNC_ORIGIN"
git -C "$SYNC_ORIGIN" symbolic-ref HEAD refs/heads/main
git clone -q "$SYNC_ORIGIN" "$SYNC_UP" 2>/dev/null
git -C "$SYNC_UP" config user.email up@test.local
git -C "$SYNC_UP" config user.name "upstream"
printf 'base\n' > "$SYNC_UP/base.txt"
printf 'shared\n' > "$SYNC_UP/shared.txt"
git -C "$SYNC_UP" add base.txt shared.txt
git -C "$SYNC_UP" commit -qm "base"
git -C "$SYNC_UP" push -q origin main

git clone -q "$SYNC_ORIGIN" "$SYNC_DEV"
git -C "$SYNC_DEV" config user.email dev@test.local
git -C "$SYNC_DEV" config user.name "dev"
cp -R "$HOOKS_DIR" "$SYNC_DEV/.githooks"
bash "$SYNC_DEV/.githooks/install.sh" >/dev/null
git -C "$SYNC_DEV" checkout -q -b dev

sync_dev() { git -C "$SYNC_DEV" "$@"; }
sync_up() { git -C "$SYNC_UP" "$@"; }
sync_parents() { sync_dev rev-list --parents -n 1 HEAD | wc -w | tr -d ' '; }
sync_head() { sync_dev rev-parse HEAD; }
sync_staged() { sync_dev diff --cached --name-only | tr '\n' ' '; }

printf 'dev only\n' > "$SYNC_DEV/dev.txt"
sync_dev add dev.txt
if sync_dev commit -qm "dev work" >/dev/null 2>&1; then
  ok "远端没有新提交时正常提交"
else
  bad "远端没有新提交时的普通提交被误拦"
fi
[ "$(sync_parents)" = "2" ] && ok "没有新提交时不会产生 merge commit" || bad "无新提交时父提交数异常"

# 主干前进（改的是别的文件）：提交会被取消一次，但主干已经合并好
printf 'upstream\n' > "$SYNC_UP/up.txt"
sync_up add up.txt
sync_up commit -qm "upstream change"
sync_up push -q origin main
printf 'dev b\n' > "$SYNC_DEV/b.txt"
sync_dev add b.txt
out="$(sync_dev commit -m "dev change" 2>&1)"
rc=$?
if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -q "重新执行一次刚才的提交命令"; then
  ok "主干有新提交：自动合并并取消本次提交（提示重跑）"
else
  bad "主干有新提交时的行为不符合预期" "$out"
fi
[ "$(sync_parents)" = "3" ] && ok "自动合并生成了 merge commit（两个父提交）" || bad "自动合并没有产生 merge commit"
[ -f "$SYNC_DEV/up.txt" ] && ok "主干的新文件已合并进分支" || bad "主干的新文件没有合并进来"
[ "$(sync_staged)" = "b.txt " ] && ok "被取消的提交内容仍留在暂存区" || bad "暂存内容丢失" "$(sync_dev status --short | tr '\n' '|')"
if sync_dev commit -m "dev change" >/dev/null 2>&1; then
  ok "重跑同一条提交命令后成功"
else
  bad "重跑提交失败" "$(sync_dev status --short | tr '\n' '|')"
fi
[ "$(sync_parents)" = "2" ] && ok "重跑后的提交是普通提交" || bad "重跑后的提交父提交数异常"
sync_dev show --stat --oneline HEAD | grep -q ' b.txt' &&
  ok "重跑后用户的改动进了提交" || bad "重跑后用户的改动没进提交"

# 主干和暂存区改了同一个文件：不自动合并，交给人工
printf 'shared upstream\n' > "$SYNC_UP/shared.txt"
sync_up commit -qam "upstream edit shared"
sync_up push -q origin main
printf 'shared dev\n' > "$SYNC_DEV/shared.txt"
sync_dev add shared.txt
head_before="$(sync_head)"
out="$(sync_dev commit -m "dev edit shared" 2>&1)"
rc=$?
if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -q "都改了"; then
  ok "主干与暂存区改同一文件时不自动合并、拒绝提交"
else
  bad "重叠文件时的处理不符合预期" "$out"
fi
[ "$(sync_head)" = "$head_before" ] && ok "重叠时没有改动分支" || bad "重叠时分支被改动"
sync_dev rev-parse -q --verify MERGE_HEAD >/dev/null 2>&1 &&
  { bad "重叠时留下了合并状态"; sync_dev merge --abort >/dev/null 2>&1 || true; } ||
  ok "重叠时没有残留合并状态"
sync_dev reset -q
sync_dev checkout -q -- shared.txt

# RAL_SYNC_STRATEGY=check：只提示，不自动合并
printf 'upstream2\n' > "$SYNC_UP/up2.txt"
sync_up add up2.txt
sync_up commit -qm "upstream again"
sync_up push -q origin main
head_before="$(sync_head)"
printf 'dev c\n' > "$SYNC_DEV/c.txt"
sync_dev add c.txt
out="$(RAL_SYNC_STRATEGY=check sync_dev commit -m "check strategy" 2>&1)"
rc=$?
if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -q "RAL_SYNC_STRATEGY=check"; then
  ok "RAL_SYNC_STRATEGY=check 时不自动合并、拒绝提交"
else
  bad "check 策略没有拒绝提交" "$out"
fi
[ "$(sync_head)" = "$head_before" ] && ok "check 策略下没有改动分支" || bad "check 策略下分支被改动"
sync_dev reset -q

# 远端拉不动时不能拦提交
printf 'dev d\n' > "$SYNC_DEV/d.txt"
sync_dev add d.txt
out="$(RAL_SYNC_BRANCH=no-such-branch sync_dev commit -m "unreachable remote" 2>&1)"
rc=$?
if [ "$rc" -eq 0 ] && printf '%s' "$out" | grep -q "跳过主干检查"; then
  ok "拉取失败时只警告、不拦提交"
else
  bad "拉取失败时的处理不符合预期" "$out"
fi

# RAL_SYNC_ENABLED=0：完全不检查远端
printf 'dev e\n' > "$SYNC_DEV/e.txt"
sync_dev add e.txt
out="$(RAL_SYNC_ENABLED=0 sync_dev commit -m "sync off" 2>&1)"
rc=$?
if [ "$rc" -eq 0 ] && [ "$(sync_parents)" = "2" ]; then
  ok "RAL_SYNC_ENABLED=0 时不做任何合并"
else
  bad "关闭同步后仍然动了分支" "$out"
fi

# 主干带进来违规文件（二进制）时撤销合并
printf '\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR' > "$SYNC_UP/logo.png"
sync_up add -f logo.png
sync_up commit -qm "upstream adds png"
sync_up push -q origin main
printf 'dev f\n' > "$SYNC_DEV/f.txt"
sync_dev add f.txt
head_before="$(sync_head)"
out="$(sync_dev commit -m "dev f" 2>&1)"
rc=$?
if [ "$rc" -ne 0 ] && printf '%s' "$out" | grep -q "已撤销这次合并"; then
  ok "主干带进来二进制文件时撤销合并并拒绝提交"
else
  bad "主干违规文件时的处理不符合预期" "$out"
fi
[ "$(sync_head)" = "$head_before" ] && ok "撤销合并后分支没有被改动" || bad "撤销合并后分支被改动"
[ -f "$SYNC_DEV/logo.png" ] && bad "撤销合并后工作区残留了主干的文件" || ok "撤销合并后工作区没有残留"
[ "$(sync_staged)" = "f.txt " ] && ok "撤销合并后暂存内容仍在" || bad "撤销合并后暂存内容丢失"
sync_dev reset -q
printf '结果：%d 通过 / %d 失败\n' "$PASS" "$FAIL"
[ "$FAIL" -eq 0 ] || exit 1
