from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

from .scanner import format_bytes


def use_color() -> bool:
    return sys.stdout.isatty() or sys.stderr.isatty()


def _c(text: str, code: str) -> str:
    if not use_color():
        return text
    return f"\033[{code}m{text}\033[0m"


def bold(t: str) -> str:
    return _c(t, "1")


def dim(t: str) -> str:
    return _c(t, "2")


def green(t: str) -> str:
    return _c(t, "32")


def yellow(t: str) -> str:
    return _c(t, "33")


def red(t: str) -> str:
    return _c(t, "31")


def cyan(t: str) -> str:
    return _c(t, "36")


def short_path(path: Path | str) -> str:
    p = Path(path).expanduser()
    try:
        return "~" + str(p.relative_to(Path.home()))
    except ValueError:
        return str(p)


class Progress:
    """Simple progress bar written to stderr."""

    def __init__(self, total: int, label: str = "Working") -> None:
        self.total = max(total, 1)
        self.label = label
        self.current = 0
        self._tty = sys.stderr.isatty()
        self._last_msg = ""

    def update(self, current: int, item: str = "") -> None:
        self.current = current
        self._last_msg = item
        if self._tty:
            self._draw()
        elif current == 1 or current == self.total or current % 5 == 0:
            sys.stderr.write(f"{self.label}: {current}/{self.total} {item}\n")
            sys.stderr.flush()

    def _draw(self) -> None:
        pct = self.current / self.total
        width = 28
        filled = int(width * pct)
        bar = "█" * filled + "░" * (width - filled)
        item = (self._last_msg[:36] + "…") if len(self._last_msg) > 37 else self._last_msg
        line = f"\r{dim(self.label)} [{bar}] {self.current}/{self.total}"
        if item:
            line += f"  {item}"
        sys.stderr.write(line.ljust(80))
        sys.stderr.flush()

    def close(self, done_msg: str = "") -> None:
        if self._tty:
            sys.stderr.write("\r" + " " * 80 + "\r")
        if done_msg:
            sys.stderr.write(done_msg + "\n")
        sys.stderr.flush()


@dataclass
class ActionResult:
    name: str
    status: str  # ok | skip | err
    size_bytes: int = 0
    detail: str = ""


@dataclass
class BatchReport:
    title: str
    results: list[ActionResult]
    dry_run: bool = False
    action: str = "archive"


def _past_tense(action: str) -> str:
    return {"archive": "archived", "restore": "restored", "clean": "cleaned"}.get(
        action, f"{action}ed"
    )


def _status_label(result: ActionResult, dry_run: bool, action: str) -> str:
    if result.status == "ok":
        if dry_run:
            return green(f"would {action}")
        return green(_past_tense(action))
    if result.status == "skip":
        return yellow("skipped")
    return red("failed")


def _status_icon(result: ActionResult) -> str:
    if result.status == "ok":
        return green("✓")
    if result.status == "skip":
        return yellow("⊘")
    return red("✗")


def print_table(headers: list[str], rows: list[list[str]], aligns: list[str] | None = None) -> None:
    aligns = aligns or ["<"] * len(headers)
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    def fmt_row(cells: list[str]) -> str:
        parts = []
        for i, cell in enumerate(cells):
            w = widths[i]
            if aligns[i] == ">":
                parts.append(cell.rjust(w))
            else:
                parts.append(cell.ljust(w))
        return "  ".join(parts)

    print(fmt_row([bold(h) for h in headers]))
    print(dim("  ".join("─" * w for w in widths)))
    for row in rows:
        print(fmt_row(row))


def render_batch_report(report: BatchReport) -> None:
    mode = dim("(dry run) ") if report.dry_run else ""
    print(f"\n{bold(report.title)} {mode}".rstrip())

    rows = []
    for r in report.results:
        detail = dim(r.detail) if r.detail else ""
        rows.append([
            r.name,
            format_bytes(r.size_bytes) if r.size_bytes else "—",
            f"{_status_icon(r)} {_status_label(r, report.dry_run, report.action)}",
            detail,
        ])
    print_table(["Project", "Size", "Status", "Note"], rows, ["<", ">", "<", "<"])

    ok = [r for r in report.results if r.status == "ok"]
    skipped = [r for r in report.results if r.status == "skip"]
    failed = [r for r in report.results if r.status == "err"]
    total_size = sum(r.size_bytes for r in ok)

    print()
    print(bold("Summary"))
    print(f"  {green('✓')} {len(ok):>3} {report.action}d     {format_bytes(total_size):>8}")
    if skipped:
        print(f"  {yellow('⊘')} {len(skipped):>3} skipped")
    if failed:
        print(f"  {red('✗')} {len(failed):>3} failed")
    print()


def render_scan_summary(
    *,
    workspace: Path,
    projects: list,
    dormant_days: int,
    cli_name: str,
) -> None:
    from .scanner import ProjectInfo  # avoid circular at module level

    total_size = sum(p.size_bytes for p in projects)
    total_cache = sum(p.cache_bytes for p in projects)

    print(bold("Workspace"))
    print(f"  {short_path(workspace)}")
    print()
    print(bold("Overview"))
    print(f"  projects   {len(projects)}")
    print(f"  total      {format_bytes(total_size)}")
    print(f"  caches     {format_bytes(total_cache)}")
    print()

    def project_status(info: ProjectInfo) -> str:
        if info.protected:
            return cyan("protected")
        if info.is_symlink:
            return dim("archived")
        if info.pinned:
            return cyan("pinned")
        if info.dormant:
            return yellow("dormant")
        return green("active")

    rows = []
    for info in sorted(projects, key=lambda p: p.size_bytes, reverse=True):
        inactive = f"{info.days_inactive}d" if info.days_inactive is not None else "?"
        rows.append([
            info.name,
            format_bytes(info.size_bytes),
            format_bytes(info.cache_bytes),
            inactive,
            project_status(info),
        ])

    print(bold("Projects"))
    print_table(["Name", "Size", "Cache", "Idle", "Status"], rows, ["<", ">", ">", ">", "<"])

    dormant = [p for p in projects if p.dormant and not p.is_symlink and not p.protected]
    if dormant:
        dormant_size = sum(p.size_bytes for p in dormant)
        print()
        print(bold("Next steps"))
        print(f"  {len(dormant)} dormant (>{dormant_days}d), {format_bytes(dormant_size)} recoverable")
        print(f"  {dim(cli_name)} archive --dormant --dry-run")
        print(f"  {dim(cli_name)} clean --dormant --dry-run")
    print()


def blue(t: str) -> str:
    return _c(t, "34")


@dataclass
class CleanEntry:
    project: str
    cache_name: str
    size_bytes: int
    status: str  # ok | skip


@dataclass
class CleanReport:
    entries: list[CleanEntry]
    dry_run: bool = False
    grouped: bool = True


def render_clean_report(report: CleanReport) -> None:
    mode = dim("(dry run) ") if report.dry_run else ""
    print(f"\n{bold('Cache cleanup')} {mode}".rstrip())

    if report.grouped:
        by_project: dict[str, list[CleanEntry]] = {}
        for e in report.entries:
            by_project.setdefault(e.project, []).append(e)

        rows = []
        for project, items in sorted(by_project.items()):
            total = sum(i.size_bytes for i in items)
            kinds = ", ".join(sorted({i.cache_name for i in items}))
            if len(kinds) > 28:
                kinds = f"{len(items)} dirs"
            rows.append([
                project,
                kinds,
                format_bytes(total),
                green("would clean") if report.dry_run else green("cleaned"),
            ])
        print_table(["Project", "Caches", "Size", "Status"], rows, ["<", "<", ">", "<"])
    else:
        rows = [
            [e.project, e.cache_name, format_bytes(e.size_bytes),
             green("would clean") if report.dry_run else green("cleaned")]
            for e in report.entries
        ]
        print_table(["Project", "Cache", "Size", "Status"], rows, ["<", "<", ">", "<"])

    total = sum(e.size_bytes for e in report.entries)
    print()
    print(bold("Summary"))
    verb = "Would free" if report.dry_run else "Freed"
    print(f"  {verb}: {green(format_bytes(total))} across {len(report.entries)} cache dir(s)")
    print()


def render_status(disks: dict[str, str]) -> None:
    print(bold("Disk usage"))
    rows = [[label, info] for label, info in disks.items()]
    print_table(["Volume", "Usage"], rows)
    print()


def render_message(kind: str, message: str) -> None:
    if kind == "ok":
        print(green(f"✓ {message}"))
    elif kind == "warn":
        print(yellow(f"⊘ {message}"))
    else:
        print(red(f"✗ {message}"))