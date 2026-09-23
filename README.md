# remote-agent-lite

[![License: MIT](https://img.shields.io/badge/license-MIT-3da639?style=flat-square)](LICENSE)[![Status: early development](https://img.shields.io/badge/status-early%20development-orange?style=flat-square)](#roadmap) [![PRs welcome](https://img.shields.io/badge/PRs-welcome-brightgreen?style=flat-square)](CONTRIBUTING.md)
[![Python 3.11+](https://img.shields.io/badge/python-3.11%2B-3776ab?style=flat-square&logo=python&logoColor=white)](https://www.python.org/) [![FastAPI](https://img.shields.io/badge/FastAPI-0.115%2B-009688?style=flat-square&logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/) [![Svelte 5](https://img.shields.io/badge/Svelte-5-ff3e00?style=flat-square&logo=svelte&logoColor=white)](https://svelte.dev/) [![Node.js 18+](https://img.shields.io/badge/node-18%2B-339933?style=flat-square&logo=nodedotjs&logoColor=white)](https://nodejs.org/) [![Target: 2C2G VPS](https://img.shields.io/badge/target-2C2G%20VPS-555555?style=flat-square)](#introduction) [![Deploy: Ubuntu 24.04](https://img.shields.io/badge/deploy-Ubuntu%2024.04-e95420?style=flat-square&logo=ubuntu&logoColor=white)](remote-agent-server/deploy/README.md)

## Introduction

remote-agent-lite is a lightweight, self-hosted suite that puts Codex on a small server — a 2C2G VPS is enough — and lets you drive that agent remotely from your phone or any other device. What it does today is close to what you get from a desktop agent client: projects, sessions and scheduled tasks.

## Roadmap

- Abstract the agent interface so that other agents can be plugged in.
- More cross-device integration, for example handing work off to a local agent seamlessly.
- Better UI and lower runtime overhead.

Pull requests and issues are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).

## Quick start

- Server development and deployment: [remote-agent-server/README.md](remote-agent-server/README.md)
- Production deployment details: [remote-agent-server/deploy/README.md](remote-agent-server/deploy/README.md)

## License

Released under the MIT License — see [LICENSE](LICENSE).
