from __future__ import annotations

import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .config import CACHE_TARGETS, Config, Registry, archive_mounted, is_protected
from .scanner import ProjectInfo, dir_size, find_cache_dirs, scan_workspace


class StorageCleanError(Exception):
    pass


def _require_archive(config: Config) -> None:
    if not archive_mounted(config.archive_path):
        raise StorageCleanError(
            f"Archive volume not mounted: {config.archive_path.parent}\n"
            "Plug in your SSD and try again."
        )
    config.archive_path.mkdir(parents=True, exist_ok=True)


def dormant_projects(config: Config) -> list[ProjectInfo]:
    projects = scan_workspace(config, Registry())
    return [
        p for p in projects
        if p.dormant and not p.is_symlink and not p.protected and not p.pinned
    ]


def archive_dormant_projects(config: Config, *, dry_run: bool = False) -> list[str]:
    """Archive all dormant projects. Returns log lines."""
    dormant = dormant_projects(config)
    if not dormant:
        return ["No dormant projects to archive."]

    if not dry_run:
        _require_archive(config)

    dormant.sort(key=lambda p: p.size_bytes, reverse=True)
    logs: list[str] = []
    total_size = 0
    archived = 0
    errors = 0

    for info in dormant:
        try:
            msg = archive_project(info.name, config, dry_run=dry_run)
            logs.append(msg)
            total_size += info.size_bytes
            archived += 1
        except StorageCleanError as e:
            logs.append(f"Skipped {info.name}: {e}")
            errors += 1

    verb = "Would archive" if dry_run else "Archived"
    logs.append(
        f"\n{verb} {archived}/{len(dormant)} dormant project(s), "
        f"{_fmt(total_size)} total"
    )
    if errors:
        logs.append(f"{errors} skipped due to errors")
    return logs


def archive_project(name: str, config: Config, *, dry_run: bool = False) -> str:
    if is_protected(name):
        raise StorageCleanError(
            f"Cannot archive {name}: protected project (storageclean must stay local)."
        )
    if not dry_run:
        _require_archive(config)
    workspace = config.workspace_path
    src = workspace / name
    dst = config.archive_path / name

    if not src.exists():
        raise StorageCleanError(f"Project not found: {name}")
    if src.is_symlink():
        raise StorageCleanError(f"{name} is already archived (symlink).")
    if dst.exists():
        raise StorageCleanError(f"Archive already exists: {dst}")

    if dry_run:
        return f"[dry-run] Would move {src} -> {dst} and create symlink"

    shutil.move(str(src), str(dst))
    src.symlink_to(dst)
    return f"Archived {name} -> {dst} (symlink at {src})"


def restore_project(name: str, config: Config, *, dry_run: bool = False) -> str:
    _require_archive(config)
    workspace = config.workspace_path
    link = workspace / name
    archived = config.archive_path / name

    if link.is_symlink():
        target = link.resolve()
        if dry_run:
            return f"[dry-run] Would restore {name}: remove symlink, move {target} -> {link}"
        link.unlink()
        shutil.move(str(target), str(link))
        return f"Restored {name} to local storage ({link})"

    if archived.exists() and not link.exists():
        if dry_run:
            return f"[dry-run] Would move {archived} -> {link}"
        shutil.move(str(archived), str(link))
        return f"Restored {name} from archive ({link})"

    raise StorageCleanError(
        f"Cannot restore {name}: not archived or not found."
    )


def pin_project(name: str, config: Config) -> str:
    if name not in config.pinned:
        config.pinned.append(name)
        config.save()
    return f"Pinned {name} (will not be auto-archived)"


def unpin_project(name: str, config: Config) -> str:
    if is_protected(name):
        raise StorageCleanError(
            f"Cannot unpin {name}: protected project (always stays local)."
        )
    if name in config.pinned:
        config.pinned.remove(name)
        config.save()
    return f"Unpinned {name}"


def clean_caches(
    config: Config,
    *,
    projects: list[str] | None = None,
    targets: list[str] | None = None,
    dormant_only: bool = False,
    dry_run: bool = False,
) -> list[str]:
    """Remove rebuildable cache dirs. Returns log lines."""
    allowed = set(targets or CACHE_TARGETS)
    workspace = config.workspace_path
    logs: list[str] = []
    total_freed = 0

    all_projects = scan_workspace(config, Registry())

    for info in all_projects:
        if projects and info.name not in projects:
            continue
        if dormant_only and not info.dormant:
            continue
        if info.pinned and dormant_only:
            continue

        project_path = info.path
        if info.is_symlink and info.symlink_target:
            project_path = info.symlink_target

        if not project_path.exists():
            continue

        hits = find_cache_dirs(project_path)
        for hit in hits:
            if hit.name not in allowed:
                continue
            if dry_run:
                logs.append(
                    f"[dry-run] Would delete {hit.path} ({_fmt(hit.size_bytes)})"
                )
            else:
                shutil.rmtree(hit.path, ignore_errors=True)
                logs.append(f"Deleted {hit.path} ({_fmt(hit.size_bytes)})")
            total_freed += hit.size_bytes

    logs.append(f"Total {'would free' if dry_run else 'freed'}: {_fmt(total_freed)}")
    return logs


def clean_global_caches(config: Config, *, dry_run: bool = False) -> list[str]:
    """Clean user-level dev caches outside project folders."""
    home = Path.home()
    global_dirs = [
        home / ".npm",
        home / ".cache",
        home / "Library" / "Caches",
        home / ".cargo" / "registry",
        home / ".gradle" / "caches",
    ]
    logs: list[str] = []
    total = 0

    for path in global_dirs:
        if not path.exists():
            continue
        size = dir_size(path)
        if size == 0:
            continue
        if dry_run:
            logs.append(f"[dry-run] Would clean {path} ({_fmt(size)})")
        else:
            # Only remove contents, not the directory itself.
            for child in path.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
            logs.append(f"Cleaned {path} ({_fmt(size)})")
        total += size

    logs.append(f"Total {'would free' if dry_run else 'freed'}: {_fmt(total)}")
    return logs


def _fmt(n: int) -> str:
    from .scanner import format_bytes
    return format_bytes(n)


def disk_status() -> dict[str, str]:
    results: dict[str, str] = {}
    for label, path in [
        ("Internal", "/System/Volumes/Data"),
        ("Archive SSD", "/Volumes/Data's Arqila"),
        ("Workspace", str(Path.home() / "Documents" / "coding")),
    ]:
        try:
            usage = shutil.disk_usage(path)
            pct = usage.used / usage.total * 100
            results[label] = (
                f"{_fmt(usage.used)} / {_fmt(usage.total)} used "
                f"({_fmt(usage.free)} free, {pct:.0f}%)"
            )
        except OSError:
            results[label] = "not available"
    return results