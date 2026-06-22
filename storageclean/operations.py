from __future__ import annotations

import shutil
from pathlib import Path

from .config import CACHE_TARGETS, Config, Registry, archive_mounted, is_protected
from .scanner import ProjectInfo, dir_size, find_cache_dirs, scan_workspace
from .transfer import move_tree_with_progress
from .ui import ActionResult, BatchReport, CleanEntry, CleanReport, TransferProgress


class StorageCleanError(Exception):
    pass


def _require_archive(config: Config) -> None:
    if not archive_mounted(config.archive_path):
        raise StorageCleanError(
            f"Archive volume not mounted: {config.archive_path.parent}\n"
            "Plug in your SSD and try again."
        )
    config.archive_path.mkdir(parents=True, exist_ok=True)


def dormant_projects(
    config: Config,
    on_progress=None,
) -> list[ProjectInfo]:
    projects = scan_workspace(config, Registry(), on_progress=on_progress)
    return [
        p for p in projects
        if p.dormant and not p.is_symlink and not p.protected and not p.pinned
    ]


def archive_dormant_projects(
    config: Config,
    *,
    dry_run: bool = False,
    on_progress=None,
    dormant: list[ProjectInfo] | None = None,
    transfer: TransferProgress | None = None,
) -> BatchReport:
    if dormant is None:
        dormant = dormant_projects(config)
    if not dormant:
        return BatchReport("Archive dormant projects", [], dry_run=dry_run)

    if not dry_run:
        _require_archive(config)

    dormant.sort(key=lambda p: p.size_bytes, reverse=True)
    results: list[ActionResult] = []
    total = len(dormant)

    for i, info in enumerate(dormant, 1):
        if transfer:
            transfer.set_batch(i, total)
            transfer.set_item(info.name)
        elif on_progress:
            on_progress(i, total, info.name)
        try:
            archive_project(info.name, config, dry_run=dry_run, transfer=transfer)
            results.append(ActionResult(info.name, "ok", info.size_bytes))
            if transfer and not dry_run:
                transfer._line.clear(f"  + {info.name}  ({_fmt(info.size_bytes)})")
        except StorageCleanError as e:
            if transfer and not dry_run:
                transfer._line.clear(f"  - {info.name}  (skipped)")
            detail = str(e).split(": ", 1)[-1] if ": " in str(e) else str(e)
            results.append(ActionResult(info.name, "skip", info.size_bytes, detail))

    title = "Archive dormant projects"
    return BatchReport(title, results, dry_run=dry_run)


def archive_project(
    name: str,
    config: Config,
    *,
    dry_run: bool = False,
    transfer: TransferProgress | None = None,
) -> ActionResult:
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
        raise StorageCleanError(f"already on SSD")

    if dry_run:
        size = dir_size(src)
        return ActionResult(name, "ok", size)

    size = dir_size(src)
    move_tree_with_progress(src, dst, progress=transfer)
    src.symlink_to(dst)
    return ActionResult(name, "ok", size)


def restore_project(
    name: str,
    config: Config,
    *,
    dry_run: bool = False,
    transfer: TransferProgress | None = None,
) -> ActionResult:
    _require_archive(config)
    workspace = config.workspace_path
    link = workspace / name
    archived = config.archive_path / name

    if link.is_symlink():
        target = link.resolve()
        size = dir_size(target) if target.exists() else 0
        if dry_run:
            return ActionResult(name, "ok", size, "remove symlink, move back")
        link.unlink()
        move_tree_with_progress(target, link, progress=transfer)
        return ActionResult(name, "ok", size)

    if archived.exists() and not link.exists():
        size = dir_size(archived)
        if dry_run:
            return ActionResult(name, "ok", size)
        move_tree_with_progress(archived, link, progress=transfer)
        return ActionResult(name, "ok", size)

    raise StorageCleanError(f"Cannot restore {name}: not archived or not found.")


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
    on_progress=None,
) -> CleanReport:
    allowed = set(targets or CACHE_TARGETS)
    entries: list[CleanEntry] = []

    all_projects = scan_workspace(config, Registry(), on_progress=on_progress)
    candidates = []
    for info in all_projects:
        if projects and info.name not in projects:
            continue
        if dormant_only and not info.dormant:
            continue
        if info.pinned and dormant_only:
            continue
        candidates.append(info)

    total = len(candidates)
    for i, info in enumerate(candidates, 1):
        if on_progress and total:
            on_progress(i, total, info.name)

        project_path = info.path
        if info.is_symlink and info.symlink_target:
            project_path = info.symlink_target
        if not project_path.exists():
            continue

        for hit in find_cache_dirs(project_path):
            if hit.name not in allowed:
                continue
            if not dry_run:
                shutil.rmtree(hit.path, ignore_errors=True)
            entries.append(
                CleanEntry(hit.project, hit.name, hit.size_bytes, "ok")
            )

    return CleanReport(entries, dry_run=dry_run)


def clean_global_caches(
    config: Config,
    *,
    dry_run: bool = False,
    on_progress=None,
) -> CleanReport:
    home = Path.home()
    global_dirs = [
        ("~/.npm", home / ".npm"),
        ("~/.cache", home / ".cache"),
        ("~/Library/Caches", home / "Library" / "Caches"),
        ("~/.cargo/registry", home / ".cargo" / "registry"),
        ("~/.gradle/caches", home / ".gradle" / "caches"),
    ]
    entries: list[CleanEntry] = []
    existing = [(label, p) for label, p in global_dirs if p.exists()]
    total = len(existing)

    for i, (label, path) in enumerate(existing, 1):
        if on_progress:
            on_progress(i, total, label)
        size = dir_size(path)
        if size == 0:
            continue
        if not dry_run:
            for child in path.iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
        entries.append(CleanEntry(label, "global", size, "ok"))

    return CleanReport(entries, dry_run=dry_run, grouped=False)


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
                f"{_fmt(usage.used)} / {_fmt(usage.total)}  ·  "
                f"{_fmt(usage.free)} free  ·  {pct:.0f}%"
            )
        except OSError:
            results[label] = "not available"
    return results