from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

from .config import Settings
from .context import write_project_agents
from .db import Database
from .git_ops import GitService
from .utils import (
    days_from_now,
    directory_size,
    free_bytes,
    iso,
    new_id,
    parse_iso,
    resolve_within,
    slugify,
    utcnow,
)


class ProjectError(ValueError):
    pass


class ProjectService:
    def __init__(self, db: Database, settings: Settings, git: GitService):
        self.db = db
        self.settings = settings
        self.git = git

    async def list_active(self) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT * FROM projects WHERE status = 'active' ORDER BY updated_at DESC"
        )
        return [await self._decorate(row) for row in rows]

    async def get(self, project_id: str, *, include_trashed: bool = False) -> dict[str, Any]:
        row = await self.db.fetchone("SELECT * FROM projects WHERE id = ?", (project_id,))
        if not row or (row["status"] != "active" and not include_trashed):
            raise FileNotFoundError("project not found")
        return dict(row)

    async def create(self, name: str) -> dict[str, Any]:
        clean_name = " ".join(name.strip().split())
        if not clean_name or len(clean_name) > 80:
            raise ProjectError("project name must be between 1 and 80 characters")
        slug = await self._unique_slug(slugify(clean_name))
        project_id = new_id()
        project_dir = self.settings.projects_dir / slug
        project_dir.mkdir(parents=True, exist_ok=False)
        try:
            (project_dir / "uploads").mkdir(parents=True, exist_ok=True)
            write_project_agents(project_dir, clean_name)
            await self.git.init_repo(project_dir, clean_name)
            now = iso()
            await self.db.execute(
                """
                INSERT INTO projects(id, name, slug, status, created_at, updated_at)
                VALUES(?, ?, ?, 'active', ?, ?)
                """,
                (project_id, clean_name, slug, now, now),
            )
        except Exception:
            shutil.rmtree(project_dir, ignore_errors=True)
            raise
        return await self.get(project_id)

    async def rename(self, project_id: str, name: str) -> dict[str, Any]:
        clean_name = " ".join(name.strip().split())
        if not clean_name or len(clean_name) > 80:
            raise ProjectError("project name must be between 1 and 80 characters")
        await self.get(project_id)
        await self.db.execute(
            "UPDATE projects SET name = ?, updated_at = ? WHERE id = ?",
            (clean_name, iso(), project_id),
        )
        return await self.get(project_id)

    async def trash(self, project_id: str) -> dict[str, Any]:
        project = await self.get(project_id)
        source = await self.project_dir(project_id)
        destination = self.settings.trash_dir / "projects" / project_id
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            shutil.rmtree(destination)
        if source.exists():
            shutil.move(str(source), str(destination))
        now = utcnow()
        purge_at = days_from_now(self.settings.trash_retention_days)
        await self.db.execute(
            """
            UPDATE projects
            SET status = 'trashed', trashed_at = ?, purge_at = ?, updated_at = ?
            WHERE id = ?
            """,
            (iso(now), iso(purge_at), iso(now), project_id),
        )
        await self.db.execute(
            "UPDATE sessions SET status = 'trashed', updated_at = ? WHERE project_id = ?",
            (iso(), project_id),
        )
        return await self.get(project_id, include_trashed=True)

    async def restore(self, project_id: str) -> dict[str, Any]:
        project = await self.get(project_id, include_trashed=True)
        if project["status"] != "trashed":
            return project
        source = self.settings.trash_dir / "projects" / project_id
        destination = self.settings.projects_dir / project["slug"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            raise ProjectError("project directory already exists")
        if source.exists():
            shutil.move(str(source), str(destination))
        now = iso()
        await self.db.execute(
            """
            UPDATE projects
            SET status = 'active', trashed_at = NULL, purge_at = NULL, updated_at = ?
            WHERE id = ?
            """,
            (now, project_id),
        )
        await self.db.execute(
            "UPDATE sessions SET status = 'active', updated_at = ? WHERE project_id = ?",
            (now, project_id),
        )
        return await self.get(project_id)

    async def purge(self, project_id: str, *, include_active: bool = False) -> None:
        project = await self.get(project_id, include_trashed=True)
        if project["status"] == "active" and not include_active:
            raise ProjectError("active project cannot be purged")
        directory = (
            self.settings.projects_dir / project["slug"]
            if project["status"] == "active"
            else self.settings.trash_dir / "projects" / project_id
        )
        if directory.exists():
            shutil.rmtree(directory, ignore_errors=True)
        await self.db.execute("DELETE FROM projects WHERE id = ?", (project_id,))

    async def list_trash(self) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT * FROM projects WHERE status = 'trashed' ORDER BY trashed_at DESC"
        )
        return [dict(row) for row in rows]

    async def empty_trash(self) -> int:
        rows = await self.db.fetchall(
            "SELECT id FROM projects WHERE status = 'trashed' ORDER BY trashed_at"
        )
        for row in rows:
            await self.purge(row["id"])
        return len(rows)

    async def purge_expired(self) -> int:
        rows = await self.db.fetchall(
            """
            SELECT id FROM projects
            WHERE status = 'trashed' AND purge_at IS NOT NULL AND purge_at <= ?
            ORDER BY purge_at
            """,
            (iso(),),
        )
        for row in rows:
            await self.purge(row["id"])
        return len(rows)

    async def purge_for_disk_pressure(self) -> int:
        removed = 0
        if free_bytes(self.settings.projects_dir) >= self.settings.disk_low_watermark:
            return 0
        rows = await self.db.fetchall(
            "SELECT id FROM projects WHERE status = 'trashed' ORDER BY trashed_at"
        )
        for row in rows:
            await self.purge(row["id"])
            removed += 1
            if free_bytes(self.settings.projects_dir) >= self.settings.disk_low_watermark:
                break
        return removed

    async def can_accept_bytes(self, project_id: str, extra_bytes: int) -> tuple[bool, str]:
        free = free_bytes(self.settings.projects_dir)
        if free - extra_bytes < self.settings.disk_critical:
            return False, "服务器磁盘剩余空间不足"
        directory = await self.project_dir(project_id)
        used = directory_size(directory, limit=self.settings.project_quota)
        if used + extra_bytes > self.settings.project_quota:
            return False, "项目磁盘配额已用完"
        return True, ""

    async def project_dir(self, project_id: str) -> Path:
        project = await self.get(project_id, include_trashed=True)
        if project["status"] == "trashed":
            return self.settings.trash_dir / "projects" / project_id
        return self.settings.projects_dir / project["slug"]

    async def resolve_file(self, project_id: str, relative: str) -> Path:
        directory = await self.project_dir(project_id)
        return resolve_within(directory, relative)

    async def _decorate(self, row: Any) -> dict[str, Any]:
        project = dict(row)
        project["session_count"] = int(
            await self.db.scalar(
                "SELECT COUNT(*) FROM sessions WHERE project_id = ? AND status = 'active'",
                (project["id"],),
            )
            or 0
        )
        directory = self.settings.projects_dir / project["slug"]
        if directory.exists():
            size = await asyncio.to_thread(directory_size, directory)
            project["size_bytes"] = size
            project["size_mb"] = round(size / (1024 * 1024), 1)
        else:
            project["size_bytes"] = 0
            project["size_mb"] = 0
        return project

    async def _unique_slug(self, base: str) -> str:
        slug = base
        for index in range(1, 10_000):
            exists = await self.db.scalar("SELECT 1 FROM projects WHERE slug = ?", (slug,))
            if not exists:
                return slug
            slug = f"{base}-{index}"
        raise ProjectError("could not allocate project slug")
