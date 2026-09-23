from __future__ import annotations

import hashlib
import re
import secrets
import shutil
import unicodedata
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(dt: datetime | None = None) -> str:
    value = dt or utcnow()
    return value.astimezone(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def hours_from_now(hours: int) -> datetime:
    return utcnow() + timedelta(hours=hours)


def new_id() -> str:
    return str(uuid.uuid4())


def random_token(bytes_count: int = 32) -> str:
    return secrets.token_urlsafe(bytes_count)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def slugify(name: str, fallback: str | None = None) -> str:
    normalized = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    slug = slug[:48].strip("-")
    if not slug:
        slug = fallback or f"project-{secrets.token_hex(4)}"
    return slug or "project"


_CONTROL_CHARS = re.compile(r"[\x00-\x1f\x7f]")


def sanitize_filename(name: str) -> str:
    raw = Path(name).name
    raw = _CONTROL_CHARS.sub("", raw).strip()
    raw = raw.replace("/", "_").replace("\\", "_")
    if raw in {"", ".", ".."}:
        raw = "file"
    encoded = raw.encode("utf-8")
    if len(encoded) > 240:
        suffix = Path(raw).suffix[:20]
        stem = Path(raw).stem.encode("utf-8")[: 240 - len(suffix.encode("utf-8"))]
        raw = stem.decode("utf-8", "ignore") + suffix
    return raw


def safe_relative_path(value: str, *, allow_empty: bool = True) -> str:
    if value is None:
        value = ""
    value = value.strip().replace("\\", "/")
    if value.startswith("/"):
        raise ValueError("absolute paths are not allowed")
    parts = [part for part in value.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        raise ValueError("parent traversal is not allowed")
    if not parts and not allow_empty:
        raise ValueError("path is required")
    return "/".join(parts)


def resolve_within(root: Path, relative: str, *, must_exist: bool = False) -> Path:
    safe = safe_relative_path(relative)
    root_resolved = root.resolve()
    candidate = (root_resolved / safe).resolve()
    try:
        candidate.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError("path escapes project root") from exc
    if must_exist and not candidate.exists():
        raise FileNotFoundError(safe)
    return candidate


def directory_size(path: Path, *, limit: int | None = None) -> int:
    total = 0
    if not path.exists():
        return 0
    for entry in path.rglob("*"):
        try:
            if entry.is_file() and not entry.is_symlink():
                total += entry.stat().st_size
        except OSError:
            continue
        if limit is not None and total > limit:
            return limit + 1
    return total


def free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def unique_path(directory: Path, filename: str) -> Path:
    target = directory / filename
    if not target.exists():
        return target
    stem = target.stem
    suffix = target.suffix
    for index in range(1, 10_000):
        candidate = directory / f"{stem} ({index}){suffix}"
        if not candidate.exists():
            return candidate
    raise RuntimeError("could not allocate a unique filename")

