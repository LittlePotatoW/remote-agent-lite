from __future__ import annotations

from pathlib import Path

from .config import Settings
from .server_info import collect_server_info


GLOBAL_CONTEXT_HEADER = """# remote-agent-lite global operating rules

You are Codex running inside remote-agent-lite on a small shared server. The server
has 2 vCPU and about 2 GB RAM unless the live snapshot says otherwise. A single
global worker runs one user turn at a time, so another turn is waiting whenever
you keep working. Prefer finishing with a useful file and a short report.
"""


GLOBAL_CONTEXT_RULES = """## Resource discipline

- Before compilation, dependency installation, test suites, model loading, bulk file
  processing, or anything that may use more than about 256 MB RAM, run
  `remote-agent-info --json` and obey the reported budgets.
- Use `nice -n 10` and `ionice -c3` for heavy work when available. Limit build,
  test, and download parallelism to the budget returned by `remote-agent-info`.
- Do not start long-lived background processes, Docker containers, conda
  environments, databases, or web servers unless the user explicitly asks.
- Prefer prebuilt Python wheels. Create and use a project-local `.venv` for Python.
- Clean temporary files when done. Do not leave build trees, caches, or large
  temporary downloads behind.
- If available memory is below 300 MB, free disk space is below 2 GB, or a cgroup
  limit is close, stop heavy work and report the blocker. Do not retry blindly.

## Workspace discipline

- Your current working directory is the active project. Keep project work in that
  directory and in its `uploads/` subdirectory.
- Do not read or modify other projects unless the user explicitly asks you to cross
  that boundary.
- User-uploaded files appear in `uploads/`. Produce deliverables as ordinary files
  in the project so the user can download them.
- Never print secrets, API keys, tokens, or the contents of environment files.

## Communication discipline

- Reply in the user's language. Keep progress updates short.
- Do not narrate every command. At the end, say what changed, where the files are,
  and what verification you ran.
- If a root/system change is required, stop and ask the user to perform it; you are
  not allowed to use sudo or manage systemd.

## Showing images

- To let the user see an image, write the file inside the project directory and
  reference it from your reply with Markdown image syntax and a project-relative
  path, for example `![chart](output/chart.png)`.
- Only png, jpg, jpeg, gif, webp and bmp render in the web interface. Never use svg
  for something the user is supposed to look at.
"""


PROJECT_AGENTS_TEMPLATE = """# Project instructions

This directory is one isolated remote-agent-lite project.

- Keep work inside this directory. User uploads are in `uploads/`.
- For Python work, create `.venv` here and run tools through it; do not install
  packages into the system Python.
- Prefer small, reproducible outputs and write final deliverables as files.
- Follow the global resource rules injected by the server. Run
  `remote-agent-info --json` before heavy work.
- Do not use Docker, Conda, long-lived daemons, or system package installation.
"""


def render_global_guidance(settings: Settings) -> str:
    snapshot = collect_server_info(settings)
    memory = snapshot.get("memory", {})
    disk = snapshot.get("disk", {})
    budgets = snapshot.get("budgets", {})
    lines = [
        GLOBAL_CONTEXT_HEADER,
        "Current snapshot at thread start:",
        f"- CPU cores: {snapshot.get('cpu', {}).get('count', 'unknown')}",
        f"- Memory total/available: {memory.get('total_mb', '?')} MB / "
        f"{memory.get('available_mb', '?')} MB",
        f"- Disk free: {disk.get('free_mb', '?')} MB",
        f"- Recommended parallelism: {budgets.get('recommended_parallelism', 1)}",
        "",
        GLOBAL_CONTEXT_RULES,
    ]
    return "\n".join(lines).strip() + "\n"


def render_project_agents(project_name: str) -> str:
    return f"# {project_name}\n\n{PROJECT_AGENTS_TEMPLATE}"


def sync_codex_home(settings: Settings) -> Path:
    settings.codex_home.mkdir(parents=True, exist_ok=True)
    target = settings.codex_home / "AGENTS.md"
    target.write_text(render_global_guidance(settings), encoding="utf-8")
    return target


def write_project_agents(project_dir: Path, project_name: str) -> Path:
    target = project_dir / "AGENTS.md"
    target.write_text(render_project_agents(project_name), encoding="utf-8")
    return target
