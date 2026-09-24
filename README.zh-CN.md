# remote-agent-lite

[English](README.md) | [简体中文](README.zh-CN.md)

[![License: MIT](https://img.shields.io/badge/license-MIT-3da639?style=flat-square)](LICENSE) [![Status: early development](https://img.shields.io/badge/status-early%20development-orange?style=flat-square)](#开发方向) [![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen?style=flat-square)](CONTRIBUTING.md)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776ab?style=flat-square&logo=python&logoColor=white)](https://www.python.org/) [![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/) [![Svelte 5](https://img.shields.io/badge/Svelte-5-ff3e00?style=flat-square&logo=svelte&logoColor=white)](https://svelte.dev/) [![Node.js 18+](https://img.shields.io/badge/node-18%2B-339933?style=flat-square&logo=nodedotjs&logoColor=white)](https://nodejs.org/) [![Target: 2C2G VPS](https://img.shields.io/badge/target-2C2G%20VPS-555555?style=flat-square)](#简介) [![Deploy: Ubuntu 24.04](https://img.shields.io/badge/deploy-Ubuntu%2024.04-e95420?style=flat-square&logo=ubuntu&logoColor=white)](remote-agent-server/deploy/README.md)

## 简介

remote-agent-lite 是一个可以将 Codex 部署在 2C2G 这类小型服务器上，并通过手机等移动设备远程访问、控制 agent 的轻量级服务套件。目前已经支持的功能类似于常见的 agent 桌面端，包括项目、会话、定时任务等。

## 开发方向

- 抽象 agent 接口，以适配更多 agent。
- 增加更多跨端联动，比如与本地的 agent 无缝衔接。
- 优化界面与运行开销。

欢迎 PR 与 issue，参见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 快速开始

- 服务器端的开发与部署：[remote-agent-server/README.md](remote-agent-server/README.md)
- 服务器端生产部署细节：[remote-agent-server/deploy/README.md](remote-agent-server/deploy/README.md)

## 许可协议

本项目以 MIT 协议发布，详见 [LICENSE](LICENSE)。
