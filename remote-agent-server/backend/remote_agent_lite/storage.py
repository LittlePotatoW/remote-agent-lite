from __future__ import annotations

import asyncio
import os
import shutil
import time
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .config import Settings
from .db import Database
from .projects import ProjectService
from .utils import (
    free_bytes,
    hours_from_now,
    iso,
    new_id,
    resolve_within,
    safe_relative_path,
    sanitize_filename,
    sha256_file,
    unique_path,
    utcnow,
)


class StorageError(ValueError):
    pass


#: 打进 zip 时直接「store」的扩展名：这些格式自己已经压过了，再 deflate 一遍只烧 CPU。
ARCHIVE_STORED_SUFFIXES = frozenset(
    {
        ".jpg", ".jpeg", ".png", ".gif", ".webp", ".avif",
        ".mp4", ".m4v", ".mov", ".mkv", ".webm",
        ".mp3", ".m4a", ".aac", ".ogg", ".opus",
        ".zip", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar",
        ".woff", ".woff2", ".docx", ".xlsx", ".pptx",
    }
)

#: 打包用的临时 zip 前缀（放在 var/uploads/ 下，不进项目目录）。
ARCHIVE_PREFIX = "archive-"
ARCHIVE_MAX_AGE_SECONDS = 24 * 3600

IMAGE_MEDIA_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
}


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

    async def resolve_image(self, project_id: str, relative: str) -> Path:
        root = await self.projects.project_dir(project_id)
        if self._has_hidden_part(relative):
            raise StorageError("这个文件由 remote-agent-lite 管理，不能预览")
        target = resolve_within(root, relative, must_exist=True)
        if not target.is_file() or target.is_symlink():
            raise StorageError("不是普通文件")
        if target.suffix.lower() not in IMAGE_MEDIA_TYPES:
            raise StorageError("只支持 png / jpg / gif / webp / bmp 图片")
        return target

    async def archive_entry(self, project_id: str, relative: str) -> tuple[Path, str]:
        """把一个文件/文件夹打包成临时 zip，返回 (临时 zip 路径, 下载文件名)。

        临时 zip 落在 `var/uploads/` 下，不进项目目录；`archive_entry` 之后由路由
        在响应结束时删掉，所以服务器上不会留下副本。
        """

        root = await self.projects.project_dir(project_id)
        if self._has_hidden_part(relative):
            raise StorageError("这个条目由 remote-agent-lite 管理，不能打包")
        source = resolve_within(root, relative, must_exist=True)
        if source.is_symlink():
            raise StorageError("不能打包符号链接")
        # 打包期间临时 zip 会占磁盘，先按未压缩大小预检一次余量
        if free_bytes(self.settings.projects_dir) - unpacked_size(source) < self.settings.disk_critical:
            raise StorageError("服务器磁盘剩余空间不足")
        self.settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        archive = self.settings.uploads_dir / f"{ARCHIVE_PREFIX}{new_id()}.zip"
        try:
            await asyncio.to_thread(_write_archive, source, archive)
        except Exception:
            archive.unlink(missing_ok=True)
            raise
        name = source.name or "archive"
        return archive, f"{name}.zip"

    async def cleanup_stale_archives(self, max_age_seconds: int = ARCHIVE_MAX_AGE_SECONDS) -> int:
        """清掉下载中断/进程被杀留下的临时 zip。"""

        cutoff = time.time() - max_age_seconds
        removed = 0
        if not self.settings.uploads_dir.exists():
            return 0
        for item in self.settings.uploads_dir.glob(f"{ARCHIVE_PREFIX}*.zip"):
            try:
                if item.stat().st_mtime < cutoff:
                    item.unlink()
                    removed += 1
            except OSError:
                continue
        return removed

    @classmethod
    def _has_hidden_part(cls, relative: str) -> bool:
        return any(part in cls.HIDDEN_NAMES for part in relative.replace("\\", "/").split("/"))


def _add_to_archive(bundle: zipfile.ZipFile, path: Path, arcname: str) -> None:
    """写入一个文件；已经压过的格式直接 store。"""

    method = (
        zipfile.ZIP_STORED
        if path.suffix.lower() in ARCHIVE_STORED_SUFFIXES
        else zipfile.ZIP_DEFLATED
    )
    bundle.write(path, arcname, compress_type=method)


def _write_archive(source: Path, archive: Path) -> None:
    """在线程里跑的同步打包：保留顶层名字、跳过受管目录与符号链接。"""

    base = source.parent
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, allowZip64=True) as bundle:
        if source.is_file():
            _add_to_archive(bundle, source, source.name)
            return
        # 先写一条目录记录：空文件夹也能得到一个合法的 zip
        bundle.writestr(source.name + "/", b"")
        for current, dirnames, filenames in os.walk(source):
            dirnames[:] = sorted(
                name
                for name in dirnames
                if name not in FileService.HIDDEN_NAMES
                and not (Path(current) / name).is_symlink()
            )
            # 每条目录都写进去，空文件夹在 zip 里才不会消失
            for name in dirnames:
                bundle.writestr(
                    (Path(current) / name).relative_to(base).as_posix() + "/", b""
                )
            for name in sorted(filenames):
                item = Path(current) / name
                if item.is_symlink() or not item.is_file():
                    continue
                _add_to_archive(bundle, item, item.relative_to(base).as_posix())


def unpacked_size(path: Path) -> int:
    """估算条目未压缩时的大小（跳过受管目录与符号链接）。"""

    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    for current, dirnames, filenames in os.walk(path):
        dirnames[:] = [name for name in dirnames if name not in FileService.HIDDEN_NAMES]
        for name in filenames:
            item = Path(current) / name
            if item.is_symlink():
                continue
            try:
                total += item.stat().st_size
            except OSError:
                continue
    return total


class UploadService:
    def __init__(self, db: Database, projects: ProjectService, settings: Settings):
        self.db = db
        self.projects = projects
        self.settings = settings

    async def init(
        self,
        project_id: str,
        filename: str,
        size: int,
        sha256: str | None = None,
        relative_path: str | None = None,
    ) -> dict[str, Any]:
        if size < 0 or size > self.settings.max_file_size:
            raise StorageError("文件超过服务器允许的大小")
        ok, reason = await self.projects.can_accept_bytes(project_id, size)
        if not ok:
            raise StorageError(reason)
        clean = sanitize_filename(filename)
        # 选文件夹上传时浏览器会带上 webkitRelativePath，这里只保留目录部分
        folder = self._target_folder(relative_path)
        upload_id = new_id()
        now = utcnow()
        chunk_size = self.settings.upload_chunk_size
        total_parts = max(1, (size + chunk_size - 1) // chunk_size)
        relative = f"uploads/{folder}/{clean}" if folder else f"uploads/{clean}"
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

    async def chunk_limit(self, upload_id: str) -> int:
        """这个上传会话允许的单片最大字节数（用于在读取请求体之前就限长）。"""
        upload = await self._get_upload(upload_id)
        return int(upload["chunk_size"])

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
        root_resolved = root.resolve()
        try:
            target_dir = resolve_within(root, upload["relative_path"]).parent
        except ValueError as exc:
            raise StorageError("上传路径不合法") from exc
        target_dir.mkdir(parents=True, exist_ok=True)
        target = unique_path(target_dir, sanitize_filename(upload["filename"]))
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
            "path": target.relative_to(root_resolved).as_posix(),
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

    @staticmethod
    def _target_folder(relative_path: str | None) -> str:
        """把前端给的相对路径收成 `uploads/` 下的子目录（丢掉最后的文件名那一段）。

        目录部分来自浏览器，不能直接信：绝对路径、`..` 和 `.git/`、`node_modules/`
        这类由 remote-agent-lite 管理的目录一律拒绝。
        """

        if not relative_path:
            return ""
        try:
            safe = safe_relative_path(relative_path, allow_empty=False)
        except ValueError as exc:
            raise StorageError("文件夹路径不合法") from exc
        parts = safe.split("/")[:-1]
        if any(part in FileService.HIDDEN_NAMES for part in parts):
            raise StorageError("这个目录不允许上传")
        if len("/".join(parts).encode("utf-8")) > 300:
            raise StorageError("文件夹层级太深")
        return "/".join(parts)

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
