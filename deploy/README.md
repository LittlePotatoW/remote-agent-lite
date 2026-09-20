# 生产部署

目标环境：全新 Ubuntu 24.04，2 vCPU / 2 GB RAM / 40 GB 磁盘，固定公网 IP。

## 安装

```bash
git clone <your-repo> remote-agent-lite
cd remote-agent-lite
bash deploy/install.sh
```

也可以非交互式提供参数：

```bash
RAL_INSTALL_BASE_URL="https://..." \
RAL_INSTALL_MODEL="..." \
RAL_INSTALL_API_KEY="..." \
RAL_INSTALL_ADMIN_PASSWORD="..." \
bash deploy/install.sh
```

安装脚本会：

- 创建 `remoteagent-web` 和 `remoteagent-codex` 两个无登录服务用户；
- 安装固定版本 `@openai/codex@0.146.1` 到 `/opt/remote-agent-lite/codex`；
- 写入 `/etc/remote-agent-lite/codex-config.toml` 和 `remote-agent-lite.env`；
- 初始化数据库、Codex 全局 `AGENTS.md` 和管理员密码；
- 运行 DSAPI Responses 预检，失败时停止，网页搜索不可用时自动关闭；
- 启用 `remote-agent-codex.service` 和 `remote-agent-lite.service`。

## 访问

1. 在阿里云安全组放行 TCP `8080`；
2. 浏览器打开 `http://<公网IP>:8080`；
3. 首次使用管理员密码登录。

这是 HTTP 明文方案，不要复用到有其他敏感数据的服务器。Codex 以普通用户运行，没有 root、sudo、apt 或 systemd 管理权限。

## 运维命令

```bash
journalctl -u remote-agent-lite -f
journalctl -u remote-agent-codex -f
remote-agent-info --json
/opt/remote-agent-lite/app/.venv/bin/remote-agent-lite set-password
bash deploy/upgrade.sh 0.146.1
bash deploy/uninstall.sh
```

## 目录

| 路径 | 用途 |
| --- | --- |
| `/srv/remote-agent-lite/projects` | 项目目录 |
| `/var/lib/remote-agent-lite/remote-agent-lite.db` | SQLite 数据库 |
| `/var/lib/remote-agent-lite/uploads` | 分片上传暂存 |
| `/var/lib/remote-agent-lite/codex-home` | Codex 会话和全局上下文 |
| `/etc/remote-agent-lite` | 环境变量、Codex 配置和 WebSocket token |

## 资源策略

- `remote-agent-codex.service`：`MemoryHigh=900M`、`MemoryMax=1300M`、`CPUQuota=150%`；
- `remote-agent-lite.service`：`MemoryHigh=180M`、`MemoryMax=300M`、`CPUQuota=50%`；
- 项目删除是永久删除；磁盘剩余低于 1 GB 时拒绝新上传和新 turn。

