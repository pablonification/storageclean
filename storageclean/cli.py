from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import __version__
from .config import CACHE_TARGETS, Config, Registry
from .operations import (
    StorageCleanError,
    archive_dormant_projects,
    archive_project,
    clean_caches,
    clean_global_caches,
    disk_status,
    dormant_projects,
    pin_project,
    restore_project,
    unpin_project,
)
from .scanner import format_bytes, scan_workspace, sync_registry
from .ui import (
    Progress,
    render_batch_report,
    render_clean_report,
    render_message,
    render_scan_summary,
    render_status,
)


def cmd_status(_args: argparse.Namespace) -> int:
    render_status(disk_status())
    return 0


def cmd_scan(args: argparse.Namespace) -> int:
    config = Config.load()
    registry = Registry()

    entries = []
    if config.workspace_path.exists():
        entries = sorted(
            e.name for e in config.workspace_path.iterdir()
            if e.is_dir() or e.is_symlink()
        )
    progress = Progress(len(entries), "Scanning")
    projects = scan_workspace(
        config,
        registry,
        on_progress=lambda i, t, name: progress.update(i, name),
    )
    progress.close()

    if args.sync:
        sync_registry(projects, registry)

    render_scan_summary(
        workspace=config.workspace_path,
        projects=projects,
        dormant_days=config.dormant_days,
        cli_name=_cli_name(),
    )
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
            n_entries = 0
            if config.workspace_path.exists():
                n_entries = sum(
                    1 for e in config.workspace_path.iterdir()
                    if e.is_dir() or e.is_symlink()
                )
            scan_prog = Progress(n_entries, "Scanning")
            dormant = dormant_projects(
                config,
                on_progress=lambda i, t, name: scan_prog.update(i, name),
            )
            scan_prog.close()

            arch_prog = Progress(
                len(dormant),
                "Archiving" if not args.dry_run else "Previewing",
            )
            report = archive_dormant_projects(
                config,
                dry_run=args.dry_run,
                dormant=dormant,
                on_progress=lambda i, t, name: arch_prog.update(i, name),
            )
            arch_prog.close()
            render_batch_report(report)
            return 0

        if not args.project:
            print("Error: provide a project name or use --dormant", file=sys.stderr)
            return 1

        progress = Progress(1, "Archiving" if not args.dry_run else "Previewing")
        progress.update(1, args.project)
        result = archive_project(args.project, config, dry_run=args.dry_run)
        progress.close()

        verb = "Would archive" if args.dry_run else "Archived"
        render_message("ok", f"{verb} {result.name} ({format_bytes(result.size_bytes)})")
        return 0
    except StorageCleanError as e:
        render_message("err", str(e))
        return 1


def cmd_restore(args: argparse.Namespace) -> int:
    config = Config.load()
    try:
        progress = Progress(1, "Restoring" if not args.dry_run else "Previewing")
        progress.update(1, args.project)
        result = restore_project(args.project, config, dry_run=args.dry_run)
        progress.close()
        verb = "Would restore" if args.dry_run else "Restored"
        render_message("ok", f"{verb} {result.name} ({format_bytes(result.size_bytes)})")
        return 0
    except StorageCleanError as e:
        render_message("err", str(e))
        return 1


def cmd_pin(args: argparse.Namespace) -> int:
    config = Config.load()
    render_message("ok", pin_project(args.project, config))
    return 0


def cmd_unpin(args: argparse.Namespace) -> int:
    config = Config.load()
    try:
        render_message("ok", unpin_project(args.project, config))
        return 0
    except StorageCleanError as e:
        render_message("err", str(e))
        return 1


def cmd_clean(args: argparse.Namespace) -> int:
    config = Config.load()
    label = "Cleaning" if not args.dry_run else "Previewing"

    if args.global_caches:
        progress = Progress(5, label)
        report = clean_global_caches(
            config,
            dry_run=args.dry_run,
            on_progress=lambda i, t, name: progress.update(i, name),
        )
        progress.close()
    else:
        projects = scan_workspace(config, Registry())
        if args.project:
            targets = [p for p in projects if p.name in args.project]
        elif args.dormant:
            targets = [p for p in projects if p.dormant and not p.pinned]
        else:
            targets = projects
        progress = Progress(len(targets) or 1, label)
        report = clean_caches(
            config,
            projects=args.project,
            targets=args.only,
            dormant_only=args.dormant,
            dry_run=args.dry_run,
            on_progress=lambda i, t, name: progress.update(i, name),
        )
        progress.close()

    render_clean_report(report)
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

    from .ui import bold, dim, print_table
    print(bold("Config saved"))
    print_table(
        ["Setting", "Value"],
        [
            ["workspace", config.workspace],
            ["archive", config.archive],
            ["dormant_days", str(config.dormant_days)],
            ["pinned", ", ".join(config.pinned) or dim("(none)")],
        ],
    )
    print()
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