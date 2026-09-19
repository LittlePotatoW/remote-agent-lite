from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .config import Settings
from .utils import is_text_file, run_command


GITIGNORE = """# remote-agent-lite managed ignores
uploads/
.venv/
venv/
env/
node_modules/
__pycache__/
.pytest_cache/
.mypy_cache/
.ruff_cache/
*.py[cod]
*.log
*.tmp
*.temp
.env
.env.*
dist/
build/
"""


class GitService:
    def __init__(self, settings: Settings):
        self.settings = settings

    async def init_repo(self, project_dir: Path, project_name: str) -> str | None:
        if (project_dir / ".git").exists():
            return await self.head(project_dir)
        code, _, err = await run_command(["git", "init", "-b", "main"], cwd=project_dir)
        if code != 0:
            raise RuntimeError(f"git init failed: {err.strip()}")
        await run_command(["git", "config", "user.name", "remote-agent-lite"], cwd=project_dir)
        await run_command(
            ["git", "config", "user.email", "remote-agent-lite@local"], cwd=project_dir
        )
        (project_dir / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
        readme = project_dir / "README.md"
        if not readme.exists():
            readme.write_text(f"# {project_name}\n", encoding="utf-8")
        return await self.commit_all(project_dir, "chore: initialize project")

    async def head(self, project_dir: Path) -> str | None:
        code, out, _ = await run_command(["git", "rev-parse", "HEAD"], cwd=project_dir)
        return out.strip() if code == 0 and out.strip() else None

    async def snapshot(self, project_dir: Path, message: str) -> dict[str, Any] | None:
        if not await self.is_repo(project_dir):
            return None
        code, _, err = await run_command(["git", "add", "-A"], cwd=project_dir)
        if code != 0:
            raise RuntimeError(f"git add failed: {err.strip()}")
        staged = await self._staged_files(project_dir)
        skipped: list[str] = []
        for relative in staged:
            path = project_dir / relative
            try:
                too_large = path.stat().st_size > self.settings.snapshot_max_file_size
            except OSError:
                too_large = True
            if too_large or not is_text_file(path):
                await run_command(
                    ["git", "restore", "--staged", "--", relative], cwd=project_dir
                )
                skipped.append(relative)
        remaining = await self._staged_files(project_dir)
        if not remaining:
            return {"commit": None, "message": message, "skipped": skipped, "files": []}
        commit = await self.commit_all(project_dir, message, add_all=False)
        return {"commit": commit, "message": message, "skipped": skipped, "files": remaining}

    async def commit_all(
        self, project_dir: Path, message: str, *, add_all: bool = True
    ) -> str | None:
        if add_all:
            code, _, err = await run_command(["git", "add", "-A"], cwd=project_dir)
            if code != 0:
                raise RuntimeError(f"git add failed: {err.strip()}")
        code, out, err = await run_command(
            ["git", "diff", "--cached", "--quiet"], cwd=project_dir
        )
        if code == 0:
            return await self.head(project_dir)
        code, _, err = await run_command(
            [
                "git",
                "-c",
                "user.name=remote-agent-lite",
                "-c",
                "user.email=remote-agent-lite@local",
                "commit",
                "-m",
                message,
            ],
            cwd=project_dir,
        )
        if code != 0:
            raise RuntimeError(f"git commit failed: {err.strip()}")
        return await self.head(project_dir)

    async def is_repo(self, project_dir: Path) -> bool:
        code, _, _ = await run_command(
            ["git", "rev-parse", "--is-inside-work-tree"], cwd=project_dir
        )
        return code == 0

    async def log(self, project_dir: Path, limit: int = 50) -> list[dict[str, Any]]:
        if not await self.is_repo(project_dir):
            return []
        code, out, _ = await run_command(
            [
                "git",
                "log",
                "-n",
                str(max(1, min(limit, 200))),
                "--pretty=format:%H%x1f%ct%x1f%s",
            ],
            cwd=project_dir,
        )
        if code != 0 or not out.strip():
            return []
        result: list[dict[str, Any]] = []
        for line in out.splitlines():
            parts = line.split("\x1f", 2)
            if len(parts) != 3:
                continue
            result.append({"hash": parts[0], "timestamp": int(parts[1]), "subject": parts[2]})
        return result

    async def show(self, project_dir: Path, commit: str) -> dict[str, Any]:
        if not await self.is_repo(project_dir):
            raise ValueError("project is not a git repository")
        code, out, err = await run_command(
            ["git", "show", "--stat", "--oneline", "--decorate=no", commit],
            cwd=project_dir,
        )
        if code != 0:
            raise ValueError(err.strip() or "commit not found")
        numstat_code, numstat, _ = await run_command(
            ["git", "show", "--numstat", "--format=", commit], cwd=project_dir
        )
        files: list[dict[str, Any]] = []
        if numstat_code == 0:
            for line in numstat.splitlines():
                parts = line.split("\t", 2)
                if len(parts) == 3:
                    files.append(
                        {"additions": parts[0], "deletions": parts[1], "path": parts[2]}
                    )
        return {"commit": commit, "stat": out, "files": files}

    async def restore(self, project_dir: Path, commit: str) -> str | None:
        if not await self.is_repo(project_dir):
            raise ValueError("project is not a git repository")
        code, _, err = await run_command(
            ["git", "rev-parse", "--verify", f"{commit}^{{commit}}"], cwd=project_dir
        )
        if code != 0:
            raise ValueError(err.strip() or "commit not found")
        code, _, err = await run_command(
            ["git", "restore", "--source", commit, "--staged", "--worktree", "--", "."],
            cwd=project_dir,
        )
        if code != 0:
            raise RuntimeError(f"git restore failed: {err.strip()}")
        return await self.commit_all(project_dir, f"rollback: restore to {commit[:12]}")

    async def _staged_files(self, project_dir: Path) -> list[str]:
        code, out, _ = await run_command(
            ["git", "diff", "--cached", "--name-only", "-z"], cwd=project_dir
        )
        if code != 0:
            return []
        return [item for item in out.split("\x00") if item]
