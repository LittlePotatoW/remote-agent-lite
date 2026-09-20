# remote-agent-lite

一个面向 2C2G 云服务器的轻量远程 Codex 工作台。

服务端常驻 `codex app-server`，通过 FastAPI 提供单管理员 Web 界面。手机和电脑使用同一个响应式页面，可以创建/删除项目、上传/下载文件、管理多个会话，并只接收当前打开会话的流式消息。

## 设计边界

- 单用户、单管理员密码，HTTP 明文访问由部署者自行承担风险。
- Codex 以专用普通用户运行，取消 Codex 自身沙箱，但没有 root、sudo、apt 或 systemd 管理权限。
- 每个项目是服务器上的独立目录，项目之间靠目录、系统用户和全局规则隔离，不是强制安全沙箱。
- 同一时间只运行一个 Codex turn，其他消息排队。
- 文件内容不会主动推送，只推文件列表变更；下载时才传输内容。
- 会话输入框可以直接附带图片（选择、粘贴、拖拽）：图片在浏览器里压成 data URL 随当轮消息发给模型，
  不写入项目目录、也不进数据库。注意 Codex 自己会把当轮输入记进 `$CODEX_HOME/sessions` 的 rollout 文件，
  所以服务器磁盘上仍会留有副本；图片也没有"历史回显"，刷新后只显示 `请看这张图片。` 这样的文字。
- 不做版本管理：项目目录就是普通文件夹，改坏了要自己负责。

## 开发

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements-dev.txt
pytest
```

Windows PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
pytest
```

前端：

```bash
cd frontend
npm install
npm run build
```

本地开发可以分别启动后端和 Vite：

```bash
uvicorn remote_agent_lite.main:app --app-dir backend --reload --port 8080
cd frontend && npm run dev
```

## 生产部署

在全新 Ubuntu 24.04 上以 root 运行：

```bash
bash deploy/install.sh
```

安装脚本会创建专用用户、Python venv、systemd 服务、固定版本的 Codex、前端静态文件和访问配置，并提示输入 DSAPI 参数与管理员密码。

详细配置见 [deploy/README.md](deploy/README.md)。

