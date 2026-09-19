from __future__ import annotations

import json
import os
import platform
import shutil
import sys
from pathlib import Path
from typing import Any

from .config import Settings, get_settings


def _read_text(path: Path, default: str = "") -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return default


def _read_meminfo() -> dict[str, int]:
    result: dict[str, int] = {}
    for line in _read_text(Path("/proc/meminfo")).splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        parts = value.strip().split()
        if parts and parts[0].isdigit():
            result[key] = int(parts[0]) * 1024
    return result


def _cgroup_dir() -> Path | None:
    cgroup = _read_text(Path("/proc/self/cgroup"))
    for line in cgroup.splitlines():
        parts = line.split(":", 2)
        if len(parts) == 3 and parts[0] == "0":
            relative = parts[2].lstrip("/")
            path = Path("/sys/fs/cgroup") / relative
            if path.exists():
                return path
    return None


def _cgroup_value(name: str) -> int | None:
    directory = _cgroup_dir()
    if not directory:
        return None
    raw = _read_text(directory / name)
    if not raw or raw == "max":
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _processes(limit: int = 5) -> list[dict[str, Any]]:
    if not Path("/proc").exists():
        return []
    rows: list[dict[str, Any]] = []
    current_user = os.getuid() if hasattr(os, "getuid") else None
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = _read_text(entry / "stat")
            status = _read_text(entry / "status")
            cmdline = _read_text(entry / "cmdline").replace("\x00", " ").strip()
            if not stat or not status:
                continue
            uid_line = next((line for line in status.splitlines() if line.startswith("Uid:")), "")
            uid = int(uid_line.split()[1]) if uid_line.split()[1].isdigit() else None
            rss_kb = 0
            for line in status.splitlines():
                if line.startswith("VmRSS:"):
                    rss_kb = int(line.split()[1])
                    break
            rows.append(
                {
                    "pid": int(entry.name),
                    "rss_mb": round(rss_kb / 1024, 1),
                    "command": (cmdline or stat.split("(", 1)[1].split(")", 1)[0])[:120],
                    "same_user": uid == current_user,
                }
            )
        except (OSError, ValueError, IndexError):
            continue
    rows.sort(key=lambda item: item["rss_mb"], reverse=True)
    return rows[:limit]


def collect_server_info(settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    cpu_count = os.cpu_count() or 1
    load = os.getloadavg() if hasattr(os, "getloadavg") else (0.0, 0.0, 0.0)
    meminfo = _read_meminfo()
    total_mb = meminfo.get("MemTotal", 0) // (1024 * 1024)
    available_mb = meminfo.get("MemAvailable", meminfo.get("MemFree", 0)) // (1024 * 1024)
    cgroup_current = _cgroup_value("memory.current")
    cgroup_max = _cgroup_value("memory.max")
    if settings.projects_dir.exists():
        disk_path = settings.projects_dir
    elif settings.data_dir.exists():
        disk_path = settings.data_dir
    else:
        disk_path = Path.cwd()
    used = shutil.disk_usage(disk_path)
    free_mb = used.free // (1024 * 1024)
    cgroup_current_mb = cgroup_current // (1024 * 1024) if cgroup_current else None
    cgroup_max_mb = cgroup_max // (1024 * 1024) if cgroup_max else None
    recommended_parallelism = 1
    if available_mb >= 900 and cpu_count >= 2:
        recommended_parallelism = 2
    if available_mb < 350 or free_mb < 2048:
        recommended_parallelism = 1
    warnings: list[str] = []
    if available_mb and available_mb < 300:
        warnings.append("available memory below 300 MB")
    if free_mb < 2048:
        warnings.append("free disk below 2 GB")
    if cgroup_max_mb and cgroup_current_mb and cgroup_current_mb > int(cgroup_max_mb * 0.9):
        warnings.append("cgroup memory above 90 percent")
    return {
        "hostname": platform.node(),
        "platform": platform.platform(),
        "cpu": {"count": cpu_count, "load_1m": load[0], "load_5m": load[1], "load_15m": load[2]},
        "memory": {
            "total_mb": total_mb,
            "available_mb": available_mb,
            "cgroup_current_mb": cgroup_current_mb,
            "cgroup_max_mb": cgroup_max_mb,
        },
        "disk": {"free_mb": free_mb, "total_mb": used.total // (1024 * 1024)},
        "processes": _processes(),
        "budgets": {
            "recommended_parallelism": recommended_parallelism,
            "available_memory_mb": available_mb,
            "free_disk_mb": free_mb,
            "heavy_work_allowed": not warnings,
            "warnings": warnings,
        },
        "settings": {
            "projects_dir": str(settings.projects_dir),
            "data_dir": str(settings.data_dir),
            "max_file_size": settings.max_file_size,
            "project_quota": settings.project_quota,
        },
    }


def render_text(info: dict[str, Any]) -> str:
    cpu = info["cpu"]
    memory = info["memory"]
    disk = info["disk"]
    budgets = info["budgets"]
    lines = [
        f"host: {info['hostname']} ({info['platform']})",
        f"cpu: {cpu['count']} cores, load {cpu['load_1m']}/{cpu['load_5m']}/{cpu['load_15m']}",
        f"memory: {memory['available_mb']} MB available / {memory['total_mb']} MB total",
        f"disk: {disk['free_mb']} MB free / {disk['total_mb']} MB total",
        f"recommended parallelism: {budgets['recommended_parallelism']}",
        f"heavy work allowed: {'yes' if budgets['heavy_work_allowed'] else 'no'}",
    ]
    if budgets["warnings"]:
        lines.append("warnings: " + "; ".join(budgets["warnings"]))
    if info.get("processes"):
        lines.append("top processes:")
        for process in info["processes"]:
            marker = "*" if process.get("same_user") else " "
            lines.append(
                f"  {marker} {process['pid']:>7} {process['rss_mb']:>8.1f} MB  {process['command']}"
            )
    return "\n".join(lines)


def main() -> None:
    as_json = "--json" in sys.argv
    info = collect_server_info()
    if as_json:
        print(json.dumps(info, ensure_ascii=False, indent=2))
    else:
        print(render_text(info))


if __name__ == "__main__":
    main()
