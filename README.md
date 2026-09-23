# remote-agent-lite

面向小内存云服务器的远程 Codex 工作台。仓库按「端」分目录，各端自包含、互不干扰。

## 目录结构

| 路径 | 说明 |
| --- | --- |
| `remote-agent-server/` | 服务端：常驻 `codex app-server` + FastAPI，自带 Web 前端、部署脚本和测试 |
| `android-app/`（规划中） | 其它客户端 |

## 分端约定

- 每个端自带依赖清单、构建方式和测试，独立开发、独立发布，改一个端不会影响另一个端。
- 端与端之间只通过服务端的 HTTP API 通信，不共享代码，也不互相引用文件。
- 仓库级的东西（git 钩子、忽略规则、换行符规则）留在根目录，对所有端统一生效。

## 快速开始

- 服务端的开发与部署：[remote-agent-server/README.md](remote-agent-server/README.md)
- 生产部署细节：[remote-agent-server/deploy/README.md](remote-agent-server/deploy/README.md)
