from __future__ import annotations

import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .db import Database
from .projects import ProjectService
from .utils import (
    hours_from_now,
    iso,
    new_id,
    resolve_within,
    sanitize_filename,
    sha256_file,
    unique_path,
    utcnow,
)


class StorageError(ValueError):
    pass


class FileService:
    HIDDEN_NAMES = {".git", ".venv", "venv", "node_modules", "__pycache__"}

    def __init__(self, projects: ProjectService, settings: Settings):
        self.projects = projects
        self.settings = settings

    async def list_entries(self, project_id: str, relative: str = "") -> dict[str, Any]:
        root = await self.projects.project_dir(project_id)
        directory = resolve_within(root, relative, must_exist=True)
        if not directory.is_dir():
            raise StorageError("path is not a directory")
        entries: list[dict[str, Any]] = []
        for item in directory.iterdir():
            if item.name in self.HIDDEN_NAMES or item.is_symlink():
                continue
            try:
                stat = item.stat()
            except OSError:
                continue
            relative_path = item.relative_to(root).as_posix()
            entries.append(
                {
                    "name": item.name,
                    "path": relative_path,
                    "type": "directory" if item.is_dir() else "file",
                    "size": 0 if item.is_dir() else stat.st_size,
                    "mtime": iso(datetime.fromtimestamp(stat.st_mtime, tz=UTC)),
                }
            )
        entries.sort(key=lambda entry: (entry["type"] != "directory", entry["name"].lower()))
        return {"path": relative, "entries": entries}

    async def delete_entry(self, project_id: str, relative: str) -> str:
        root = await self.projects.project_dir(project_id)
        if self._has_hidden_part(relative):
            raise StorageError("这个条目由 remote-agent-lite 管理，不能删除")
        target = resolve_within(root, relative)
        if target == root.resolve():
            raise StorageError("不能删除项目根目录")
        if not target.exists():
            raise FileNotFoundError(relative)
        if target.is_dir():
            shutil.rmtree(target)
            return "directory"
        target.unlink()
        return "file"

    async def resolve_download(self, project_id: str, relative: str) -> Path:
        root = await self.projects.project_dir(project_id)
        if self._has_hidden_part(relative):
            raise StorageError("this file is managed by remote-agent-lite")
        target = resolve_within(root, relative, must_exist=True)
        if not target.is_file() or target.is_symlink():
            raise StorageError("path is not a regular file")
        return target

    @classmethod
    def _has_hidden_part(cls, relative: str) -> bool:
        return any(part in cls.HIDDEN_NAMES for part in relative.replace("\\", "/").split("/"))


class UploadService:
    def __init__(self, db: Database, projects: ProjectService, settings: Settings):
        self.db = db
        self.projects = projects
        self.settings = settings

    async def init(
        self, project_id: str, filename: str, size: int, sha256: str | None = None
    ) -> dict[str, Any]:
        if size < 0 or size > self.settings.max_file_size:
            raise StorageError("文件超过服务器允许的大小")
        ok, reason = await self.projects.can_accept_bytes(project_id, size)
        if not ok:
            raise StorageError(reason)
        clean = sanitize_filename(filename)
        upload_id = new_id()
        now = utcnow()
        chunk_size = self.settings.upload_chunk_size
        total_parts = max(1, (size + chunk_size - 1) // chunk_size)
        relative = f"uploads/{clean}"
        upload_dir = self.settings.uploads_dir / upload_id
        upload_dir.mkdir(parents=True, exist_ok=True)
        await self.db.execute(
            """
            INSERT INTO upload_sessions(
                id, project_id, filename, relative_path, size, sha256, chunk_size,
                total_parts, received_parts, status, created_at, updated_at, expires_at
            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, 0, 'in_progress', ?, ?, ?)
            """,
            (
                upload_id,
                project_id,
                clean,
                relative,
                size,
                sha256,
                chunk_size,
                total_parts,
                iso(now),
                iso(now),
                iso(hours_from_now(self.settings.upload_ttl_hours)),
            ),
        )
        received = await self._received_parts(upload_id)
        return {
            "upload_id": upload_id,
            "filename": clean,
            "relative_path": relative,
            "size": size,
            "chunk_size": chunk_size,
            "total_parts": total_parts,
            "received_parts": received,
            "expires_at": iso(hours_from_now(self.settings.upload_ttl_hours)),
        }

    async def put_part(self, upload_id: str, part_index: int, data: bytes) -> dict[str, Any]:
        upload = await self._get_upload(upload_id)
        if part_index < 0 or part_index >= upload["total_parts"]:
            raise StorageError("invalid upload part")
        if len(data) > upload["chunk_size"]:
            raise StorageError("upload part is too large")
        if part_index > 0:
            previous = await self.db.fetchone(
                "SELECT 1 FROM upload_parts WHERE upload_id = ? AND part_index = ?",
                (upload_id, part_index - 1),
            )
            if not previous:
                raise StorageError("previous upload part is missing")
        part_dir = self.settings.uploads_dir / upload_id
        part_dir.mkdir(parents=True, exist_ok=True)
        part_path = part_dir / f"{part_index:08d}.part"
        part_path.write_bytes(data)
        digest = __import__("hashlib").sha256(data).hexdigest()
        await self.db.execute(
            """
            INSERT INTO upload_parts(upload_id, part_index, size, sha256, created_at)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(upload_id, part_index) DO UPDATE SET
                size = excluded.size, sha256 = excluded.sha256, created_at = excluded.created_at
            """,
            (upload_id, part_index, len(data), digest, iso()),
        )
        received = await self._received_parts(upload_id)
        await self.db.execute(
            "UPDATE upload_sessions SET received_parts = ?, updated_at = ? WHERE id = ?",
            (received, iso(), upload_id),
        )
        return {"upload_id": upload_id, "part_index": part_index, "received_parts": received}

    async def complete(self, upload_id: str, sha256: str | None = None) -> dict[str, Any]:
        upload = await self._get_upload(upload_id)
        received = await self._received_parts(upload_id)
        if received != upload["total_parts"]:
            raise StorageError(
                f"upload incomplete: {received}/{upload['total_parts']} parts received"
            )
        project = await self.projects.get(upload["project_id"])
        root = await self.projects.project_dir(project["id"])
        uploads_dir = root / "uploads"
        uploads_dir.mkdir(parents=True, exist_ok=True)
        target = unique_path(uploads_dir, sanitize_filename(upload["filename"]))
        temp_target = target.with_name(target.name + ".uploading")
        part_dir = self.settings.uploads_dir / upload_id
        try:
            with temp_target.open("wb") as output:
                for index in range(upload["total_parts"]):
                    part_path = part_dir / f"{index:08d}.part"
                    with part_path.open("rb") as part:
                        shutil.copyfileobj(part, output)
            if temp_target.stat().st_size != upload["size"]:
                raise StorageError("uploaded file size does not match")
            actual_sha = await __import__("asyncio").to_thread(sha256_file, temp_target)
            expected_sha = sha256 or upload["sha256"]
            if expected_sha and expected_sha.lower() != actual_sha:
                raise StorageError("uploaded file checksum does not match")
            temp_target.replace(target)
        finally:
            if temp_target.exists():
                temp_target.unlink(missing_ok=True)
        await self.db.execute("DELETE FROM upload_sessions WHERE id = ?", (upload_id,))
        shutil.rmtree(part_dir, ignore_errors=True)
        return {
            "path": target.relative_to(root).as_posix(),
            "name": target.name,
            "size": target.stat().st_size,
            "sha256": actual_sha,
        }

    async def cleanup_expired(self) -> int:
        rows = await self.db.fetchall(
            "SELECT id FROM upload_sessions WHERE expires_at <= ?", (iso(),)
        )
        for row in rows:
            shutil.rmtree(self.settings.uploads_dir / row["id"], ignore_errors=True)
        if rows:
            await self.db.executemany(
                "DELETE FROM upload_sessions WHERE id = ?", [(row["id"],) for row in rows]
            )
        return len(rows)

    async def _get_upload(self, upload_id: str) -> dict[str, Any]:
        row = await self.db.fetchone("SELECT * FROM upload_sessions WHERE id = ?", (upload_id,))
        if not row or row["status"] != "in_progress":
            raise StorageError("upload session not found")
        return dict(row)

    async def _received_parts(self, upload_id: str) -> int:
        row = await self.db.fetchone(
            "SELECT COUNT(*) AS count FROM upload_parts WHERE upload_id = ?", (upload_id,)
        )
        return int(row["count"] if row else 0)
