# Git hooks：挡住不该进仓库的文件

`git add` 没有 pre-add 钩子，所以这里用**两个钩子配合**实现「暂存和提交都拦」：

| 钩子 | 时机 | 能力 |
| --- | --- | --- |
| `post-index-change` | 索引被写入后（`git add` / `git commit` / `git reset` …） | 不能阻止动作，但会**把违规文件自动移出暂存区**并警告 |
| `pre-commit` | 生成提交前 | **硬拦**（退出码 1，提交不会发生）；文件检查通过后还会确认分支有没有落后于远端主干 |

结论：暂存阶段是「加完立刻撤销」，提交阶段是「直接拒绝」。想绕过只有 `git commit --no-verify`（或 `RAL_AUTO_UNSTAGE=0` 只关掉暂存撤销）。

## 安装

```bash
bash .githooks/install.sh          # 写入 core.hooksPath=.githooks（只改本地仓库配置）
bash .githooks/install.sh --check  # 查看状态
bash .githooks/install.sh --uninstall
bash .githooks/run-tests.sh        # 自测：38 条用例（文件规则 + 主干同步）
```

克隆仓库后需要为每个 clone 执行一次 `install.sh`（钩子路径是本地配置，不随 clone 传播）。

## 拦截规则

| 类型 | 判定方式 |
| --- | --- |
| `binary` | 前 8 KiB 内出现 NUL 字节（与 git 自身的二进制判定一致） |
| `non-text` | 内容不是合法 UTF-8（仓库统一 UTF-8，UTF-16 / Latin-1 会被拦） |
| `large` | 单个文件超过 `RAL_MAX_BYTES`（默认 1 MiB） |
| `denylist` | 命中 `.githooks/config` 里的 `RAL_DENY_PATTERNS`：密钥 `.env`/`*.pem`/`*.key`，依赖与缓存 `.venv`/`node_modules`/`__pycache__`，构建产物 `dist`/`build`/`var`，数据库 `*.db`/`*.sqlite`，压缩包与媒体 `*.zip`/`*.png`/`*.mp4`/字体等 |
| `ignored` | 文件被 `.gitignore` 忽略，却用 `git add -f` 强行加入 |
| `symlink` | 索引模式 `120000`，符号链接 |
| `submodule` | 索引模式 `160000`，submodule / gitlink |

黑名单模式同时匹配「完整路径」「basename」和「路径中的每一段」，所以 `node_modules` 能命中 `remote-agent-server/frontend/node_modules/x.js`；`*` 会跨 `/`，`assets/*` 覆盖整个目录。

## 例外

- 路径写进 `.githooks/allowlist.txt`（每行一个 glob），即可跳过全部检查 —— 它本身在版本控制里，例外会进入代码评审。已预置 `.env.example` / `remote-agent-server/deploy/remote-agent-lite.env.example` 这类模板。
- 临时放宽标量阈值：`RAL_MAX_BYTES=10485760 git commit`、`RAL_BLOCK_SYMLINKS=0 git commit`。

## 可调项（`.githooks/config`，环境变量优先）

`RAL_MAX_BYTES`、`RAL_SAMPLE_BYTES`、`RAL_BLOCK_SYMLINKS`、`RAL_BLOCK_GITLINKS`、`RAL_BLOCK_IGNORED`、`RAL_REQUIRE_UTF8`、`RAL_AUTO_UNSTAGE`、`RAL_ALLOWLIST_FILE`、`RAL_DENY_PATTERNS`。

## 边界

- 只检查**本次改动**（相对 HEAD 的暂存内容）：仓库历史里已存在的二进制文件不会导致后续提交被拦。
- 不检查 `git commit --no-verify`、`git push` 到远端的既有对象，也不做内容机密扫描（正则找 token 之类）。
- `git commit -a` 会被正常拦截；合并 / cherry-pick 引入的文件同样按上述规则检查。
- 只依赖 `git` + `POSIX` 工具（UTF-8 校验优先用 `iconv`，其次 `python3`），需要 `bash`。

## 提交前同步远端主干

`pre-commit` 在文件检查通过后，会确认当前分支有没有落后于远端主干（默认 `origin/main`）：

1. `git fetch` 远端主干；超时或失败**只警告，不拦提交**（直连不通时可以设 `RAL_GIT_PROXY`）；
2. 当前分支已经包含主干最新提交 → 直接放行；
3. 主干有新提交 → 收起你的暂存内容 → 干净地合并主干（单独生成一个 merge commit）→ 把你的暂存内容叠回索引 → **取消本次提交**，提示你重新执行一次提交命令；重跑时主干已经合好，会直接提交成功。

为什么不能“合并完直接继续提交”：git 在 pre-commit 阶段已经锁住 HEAD，而且不把钩子里新写的 `MERGE_HEAD` 算作这次提交的父提交。上面的顺序保证你的改动不会和 merge commit 混在一条提交里，也不会丢。

两个额外的保护：

- 主干和你的暂存区改了**同一个文件**时，不自动合并，直接拒绝并提示手动 `git merge`（避免把冲突留给你）；
- 主干带进来的文件同样要过一遍上面的拦截规则，命中就撤销合并并拒绝提交。

| 配置项 | 说明 |
| --- | --- |
| `RAL_SYNC_ENABLED` | `1`（默认）启用；`0` 完全不检查远端 |
| `RAL_SYNC_REMOTE` / `RAL_SYNC_BRANCH` | 远端和主干分支名，默认 `origin` / `main` |
| `RAL_SYNC_TIMEOUT` | fetch 超时秒数，默认 25 |
| `RAL_SYNC_STRATEGY` | `merge`（默认）自动合并；`check` 只提示并拒绝本次提交 |
| `RAL_SYNC_PRINT_COMMITS` | `1`（默认）合并前打印要带进来的提交 |
| `RAL_GIT_PROXY` | 可选代理，例如 `http://127.0.0.1:8899`；直连 github.com 不通时用 |
