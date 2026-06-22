from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from datetime import datetime

from . import __version__
from .config import CACHE_TARGETS, Config, Registry
from .operations import (
    StorageCleanError,
    archive_dormant_projects,
    archive_project,
    clean_caches,
    clean_global_caches,
    disk_status,
    pin_project,
    restore_project,
    unpin_project,
)
from .scanner import format_bytes, scan_workspace, sync_registry


def _status_icon(info) -> str:
    if info.protected:
        return "🔒"
    if info.pinned:
        return "📌"
    if info.is_symlink:
        return "🔗"
    if info.dormant:
        return "💤"
    return "✅"


def cmd_status(_args: argparse.Namespace) -> int:
    print("Disk usage:")
    for label, line in disk_status().items():
        print(f"  {label:14} {line}")
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    config = Config.load()
    registry = Registry()
    projects = scan_workspace(config, registry)

    if args.sync:
        sync_registry(projects, registry)

    total_size = sum(p.size_bytes for p in projects)
    total_cache = sum(p.cache_bytes for p in projects)

    print(f"Workspace: {config.workspace_path}")
    print(f"Projects: {len(projects)} | Total: {format_bytes(total_size)} | Caches: {format_bytes(total_cache)}")
    print()
    print(f"{'':2} {'Project':<28} {'Size':>8} {'Cache':>8} {'Inactive':>9}  Status")
    print("-" * 72)

    for info in sorted(projects, key=lambda p: p.size_bytes, reverse=True):
        inactive = f"{info.days_inactive}d" if info.days_inactive is not None else "?"
        status = "archived" if info.is_symlink else ("dormant" if info.dormant else "active")
        if info.protected:
            status = "protected"
        elif info.pinned:
            status = "pinned"
        print(
            f"{_status_icon(info)} {info.name:<28} {format_bytes(info.size_bytes):>8} "
            f"{format_bytes(info.cache_bytes):>8} {inactive:>9}  {status}"
        )

    dormant = [p for p in projects if p.dormant and not p.is_symlink and not p.protected]
    if dormant:
        print()
        cli = _cli_name()
        dormant_size = sum(p.size_bytes for p in dormant)
        print(
            f"💤 {len(dormant)} dormant project(s) (>{config.dormant_days}d inactive), "
            f"{format_bytes(dormant_size)} total:"
        )
        print(f"   {cli} archive --dormant --dry-run")
        print(f"   {cli} clean --dormant --dry-run")

    return 0


def cmd_list(args: argparse.Namespace) -> int:
    config = Config.load()
    registry = Registry()
    projects = scan_workspace(config, registry)

    if args.archived:
        projects = [p for p in projects if p.is_symlink]
    elif args.dormant:
        projects = [p for p in projects if p.dormant and not p.is_symlink]
    elif args.active:
        projects = [p for p in projects if not p.is_symlink and not p.dormant]

    for info in projects:
        print(info.name)
    return 0


def cmd_archive(args: argparse.Namespace) -> int:
    config = Config.load()
    try:
        if args.dormant:
            logs = archive_dormant_projects(config, dry_run=args.dry_run)
            for line in logs:
                print(line)
            return 0
        if not args.project:
            print("Error: provide a project name or use --dormant", file=sys.stderr)
            return 1
        print(archive_project(args.project, config, dry_run=args.dry_run))
        return 0
    except StorageCleanError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_restore(args: argparse.Namespace) -> int:
    config = Config.load()
    try:
        msg = restore_project(args.project, config, dry_run=args.dry_run)
        print(msg)
        return 0
    except StorageCleanError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_pin(args: argparse.Namespace) -> int:
    config = Config.load()
    print(pin_project(args.project, config))
    return 0


def cmd_unpin(args: argparse.Namespace) -> int:
    config = Config.load()
    try:
        print(unpin_project(args.project, config))
        return 0
    except StorageCleanError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


def cmd_clean(args: argparse.Namespace) -> int:
    config = Config.load()
    projects = args.project if args.project else None
    targets = args.only if args.only else None

    if args.global_caches:
        logs = clean_global_caches(config, dry_run=args.dry_run)
    else:
        logs = clean_caches(
            config,
            projects=projects,
            targets=targets,
            dormant_only=args.dormant,
            dry_run=args.dry_run,
        )

    for line in logs:
        print(line)
    return 0


def cmd_config(args: argparse.Namespace) -> int:
    config = Config.load()
    if args.workspace:
        config.workspace = args.workspace
    if args.archive:
        config.archive = args.archive
    if args.dormant_days:
        config.dormant_days = args.dormant_days
    config.save()
    print("Config saved:")
    print(f"  workspace:    {config.workspace}")
    print(f"  archive:      {config.archive}")
    print(f"  dormant_days: {config.dormant_days}")
    print(f"  pinned:       {', '.join(config.pinned) or '(none)'}")
    return 0


def _cli_name() -> str:
    if os.environ.get("STORAGECLEAN_CLI") == "sc":
        return "sc"
    invoked = Path(sys.argv[0]).name
    return invoked if invoked in {"sc", "storageclean"} else "storageclean"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=_cli_name(),
        description="Manage coding project storage — archive dormant repos to SSD, clean rebuildable caches.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Show disk usage for internal drive, SSD, and workspace")

    scan_p = sub.add_parser("scan", help="Scan projects: size, cache, activity, dormant status")
    scan_p.add_argument("--sync", action="store_true", help="Update registry from scan results")

    list_p = sub.add_parser("list", help="List project names")
    list_p.add_argument("--active", action="store_true", help="Only active projects")
    list_p.add_argument("--dormant", action="store_true", help="Only dormant projects")
    list_p.add_argument("--archived", action="store_true", help="Only archived (symlinked) projects")

    arch_p = sub.add_parser("archive", help="Move project(s) to SSD and leave symlinks")
    arch_p.add_argument("project", nargs="?", help="Project folder name (omit with --dormant)")
    arch_p.add_argument(
        "--dormant", action="store_true",
        help="Archive all dormant projects (batch)",
    )
    arch_p.add_argument("--dry-run", action="store_true", help="Preview without moving")

    rest_p = sub.add_parser("restore", help="Bring archived project back to local disk")
    rest_p.add_argument("project", help="Project folder name")
    rest_p.add_argument("--dry-run", action="store_true", help="Preview without moving")

    pin_p = sub.add_parser("pin", help="Pin project as active (skip auto-archive)")
    pin_p.add_argument("project", help="Project folder name")

    unpin_p = sub.add_parser("unpin", help="Remove pin from project")
    unpin_p.add_argument("project", help="Project folder name")

    clean_p = sub.add_parser("clean", help="Delete rebuildable caches (node_modules, .next, etc.)")
    clean_p.add_argument(
        "--project", "-p", action="append", dest="project",
        help="Only clean specific project(s); repeatable",
    )
    clean_p.add_argument(
        "--only", action="append",
        help=f"Only delete specific cache types. Choices: {', '.join(CACHE_TARGETS)}",
    )
    clean_p.add_argument(
        "--dormant", action="store_true",
        help="Only clean caches in dormant (inactive) projects",
    )
    clean_p.add_argument(
        "--global", dest="global_caches", action="store_true",
        help="Clean user-level caches (~/.npm, ~/.cache, ~/Library/Caches, etc.)",
    )
    clean_p.add_argument("--dry-run", action="store_true", help="Preview without deleting")

    cfg_p = sub.add_parser("config", help="View or update settings")
    cfg_p.add_argument("--workspace", help="Path to coding workspace")
    cfg_p.add_argument("--archive", help="Path to archive folder on SSD")
    cfg_p.add_argument("--dormant-days", type=int, dest="dormant_days", help="Days before project is dormant")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    handlers = {
        "status": cmd_status,
        "scan": cmd_scan,
        "list": cmd_list,
        "archive": cmd_archive,
        "restore": cmd_restore,
        "pin": cmd_pin,
        "unpin": cmd_unpin,
        "clean": cmd_clean,
        "config": cmd_config,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())