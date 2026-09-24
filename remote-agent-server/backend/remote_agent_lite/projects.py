from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

from .config import Settings
from .context import write_project_agents
from .db import Database
from .utils import directory_size, free_bytes, iso, new_id, slugify


class ProjectError(ValueError):
    pass


class ProjectService:
    def __init__(self, db: Database, settings: Settings):
        self.db = db
        self.settings = settings

    async def list_active(self) -> list[dict[str, Any]]:
        rows = await self.db.fetchall(
            """
            SELECT * FROM projects
            ORDER BY pinned DESC, COALESCE(pinned_at, '') DESC, updated_at DESC
            """
        )
        return [await self._decorate(row) for row in rows]

    async def get(self, project_id: str) -> dict[str, Any]:
        row = await self.db.fetchone("SELECT * FROM projects WHERE id = ?", (project_id,))
        if not row:
            raise FileNotFoundError("项目不存在")
        project = dict(row)
        project["pinned"] = bool(project.get("pinned"))
        return project

    async def create(self, name: str) -> dict[str, Any]:
        clean_name = " ".join(name.strip().split())
        if not clean_name or len(clean_name) > 80:
            raise ProjectError("项目名需要 1 到 80 个字符")
        slug = await self._unique_slug(slugify(clean_name))
        project_id = new_id()
        project_dir = self.settings.projects_dir / slug
        project_dir.mkdir(parents=True, exist_ok=False)
        try:
            (project_dir / "uploads").mkdir(parents=True, exist_ok=True)
            write_project_agents(project_dir, clean_name)
            now = iso()
            await self.db.execute(
                """
                INSERT INTO projects(id, name, slug, pinned, pinned_at, created_at, updated_at)
                VALUES(?, ?, ?, 0, NULL, ?, ?)
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
            raise ProjectError("项目名需要 1 到 80 个字符")
        await self.get(project_id)
        await self.db.execute(
            "UPDATE projects SET name = ?, updated_at = ? WHERE id = ?",
            (clean_name, iso(), project_id),
        )
        return await self.get(project_id)

    async def set_pinned(self, project_id: str, pinned: bool) -> dict[str, Any]:
        await self.get(project_id)
        await self.db.execute(
            "UPDATE projects SET pinned = ?, pinned_at = ? WHERE id = ?",
            (1 if pinned else 0, iso() if pinned else None, project_id),
        )
        return await self.get(project_id)

    async def delete(self, project_id: str) -> None:
        project = await self.get(project_id)
        directory = self.settings.projects_dir / project["slug"]
        if directory.exists():
            shutil.rmtree(directory, ignore_errors=True)
        await self.db.execute("DELETE FROM projects WHERE id = ?", (project_id,))

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
        project = await self.get(project_id)
        return self.settings.projects_dir / project["slug"]

    async def _decorate(self, row: Any) -> dict[str, Any]:
        project = dict(row)
        project["pinned"] = bool(project.get("pinned"))
        project["session_count"] = int(
            await self.db.scalar(
                "SELECT COUNT(*) FROM sessions WHERE project_id = ?", (project["id"],)
            )
            or 0
        )
        directory = self.settings.projects_dir / project["slug"]
        if directory.exists():
            size = await asyncio.to_thread(directory_size, directory)
        else:
            size = 0
        project["size_bytes"] = size
        project["size_mb"] = round(size / (1024 * 1024), 1)
        return project

    async def _unique_slug(self, base: str) -> str:
        slug = base
        for index in range(1, 10_000):
            exists = await self.db.scalar("SELECT 1 FROM projects WHERE slug = ?", (slug,))
            if not exists:
                return slug
            slug = f"{base}-{index}"
        raise ProjectError("无法分配项目目录名")
