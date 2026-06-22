from __future__ import annotations

import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .config import CACHE_TARGETS, Config, ProjectRecord, Registry, is_protected


@dataclass
class CacheHit:
    project: str
    path: Path
    name: str
    size_bytes: int


@dataclass
class ProjectInfo:
    name: str
    path: Path
    size_bytes: int
    cache_bytes: int
    last_modified: datetime | None
    last_git_commit: datetime | None
    is_symlink: bool
    symlink_target: Path | None
    is_archived: bool
    pinned: bool
    protected: bool
    dormant: bool

    @property
    def last_activity(self) -> datetime | None:
        dates = [d for d in (self.last_modified, self.last_git_commit) if d]
        return max(dates) if dates else None

    @property
    def days_inactive(self) -> int | None:
        if not self.last_activity:
            return None
        delta = datetime.now(timezone.utc) - self.last_activity
        return delta.days


def dir_size(path: Path) -> int:
    total = 0
    try:
        for p in path.rglob("*"):
            if p.is_file():
                try:
                    total += p.stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def _git_last_commit(path: Path) -> datetime | None:
    if not (path / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "-C", str(path), "log", "-1", "--format=%cI"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return None
        return datetime.fromisoformat(result.stdout.strip().replace("Z", "+00:00"))
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return None


def _parse_mtime(path: Path) -> datetime | None:
    try:
        ts = path.stat().st_mtime
        return datetime.fromtimestamp(ts, tz=timezone.utc)
    except OSError:
        return None


def _is_cache_dir(project_path: Path, match: Path) -> str | None:
    rel = match.relative_to(project_path)
    rel_str = rel.as_posix()

    for target in CACHE_TARGETS:
        if "/" in target:
            if rel_str == target or rel_str.endswith("/" + target):
                return target.split("/")[-1]
        elif match.name == target:
            return target
    return None


def find_cache_dirs(project_path: Path) -> list[CacheHit]:
    hits: list[CacheHit] = []
    project_name = project_path.name
    seen: set[Path] = set()

    candidates: list[tuple[Path, str]] = []
    for match in project_path.rglob("*"):
        if not match.is_dir():
            continue
        try:
            match.relative_to(project_path)
        except ValueError:
            continue

        cache_name = _is_cache_dir(project_path, match)
        if cache_name:
            candidates.append((match, cache_name))

    # Keep only top-level cache dirs (skip __pycache__ inside .venv, etc.)
    candidates.sort(key=lambda x: len(x[0].parts))
    kept: list[Path] = []
    for match, cache_name in candidates:
        if any(match.is_relative_to(k) for k in kept):
            continue
        kept.append(match)
        size = dir_size(match)
        if size > 0:
            hits.append(
                CacheHit(
                    project=project_name,
                    path=match,
                    name=cache_name,
                    size_bytes=size,
                )
            )
    return hits


def scan_project(
    path: Path,
    config: Config,
    registry: Registry,
) -> ProjectInfo | None:
    if not path.exists():
        return None
    if path.name.startswith("."):
        return None

    is_symlink = path.is_symlink()
    symlink_target = path.resolve() if is_symlink else None
    archive_path = config.archive_path / path.name
    is_archived = archive_path.exists() and not path.exists()

    protected = is_protected(path.name)
    pinned = protected or path.name in config.pinned
    rec = registry.get(path.name)
    if rec and rec.pinned:
        pinned = True

    real_path = symlink_target if is_symlink and symlink_target else path
    size_bytes = dir_size(real_path) if real_path.exists() else 0

    cache_hits = find_cache_dirs(real_path) if real_path.exists() else []
    cache_bytes = sum(h.size_bytes for h in cache_hits)

    last_modified = _parse_mtime(real_path if real_path.exists() else path)
    last_git = _git_last_commit(real_path) if real_path.exists() else None

    info = ProjectInfo(
        name=path.name,
        path=path,
        size_bytes=size_bytes,
        cache_bytes=cache_bytes,
        last_modified=last_modified,
        last_git_commit=last_git,
        is_symlink=is_symlink,
        symlink_target=symlink_target,
        is_archived=is_archived,
        pinned=pinned,
        protected=protected,
        dormant=False,
    )

    if not pinned and info.days_inactive is not None:
        info.dormant = info.days_inactive >= config.dormant_days

    return info


def scan_workspace(config: Config, registry: Registry) -> list[ProjectInfo]:
    workspace = config.workspace_path
    if not workspace.exists():
        return []

    projects: list[ProjectInfo] = []
    for entry in sorted(workspace.iterdir()):
        if not entry.is_dir() and not entry.is_symlink():
            continue
        info = scan_project(entry, config, registry)
        if info:
            projects.append(info)
    return projects


def sync_registry(projects: list[ProjectInfo], registry: Registry) -> None:
    for info in projects:
        status = "active"
        if info.is_symlink:
            status = "symlinked"
        elif info.is_archived:
            status = "archived"

        registry.upsert(
            ProjectRecord(
                name=info.name,
                status=status,
                size_bytes=info.size_bytes,
                last_activity=info.last_activity.isoformat() if info.last_activity else None,
                last_git_commit=info.last_git_commit.isoformat() if info.last_git_commit else None,
                pinned=info.pinned,
                cache_bytes=info.cache_bytes,
            )
        )


def format_bytes(n: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(n)
    for unit in units:
        if size < 1024 or unit == "TB":
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"