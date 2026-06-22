from __future__ import annotations

import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from .scanner import format_bytes

_ANSI_RE = re.compile(r"\033\[[0-9;]*m")
_CLEAR = "\033[2K\r"


def _live_capable(stream) -> bool:
    if os.environ.get("SC_NO_PROGRESS") == "1":
        return False
    if os.environ.get("SC_FORCE_PROGRESS") == "1":
        return True
    return stream.isatty() and os.environ.get("TERM", "dumb") != "dumb"


def _bar(pct: float, width: int = 20) -> str:
    pct = max(0.0, min(1.0, pct))
    filled = int(width * pct)
    return "[" + "#" * filled + "-" * (width - filled) + "]"


class LiveLine:
    """Single-line live status with safe clear + throttling."""

    def __init__(self, stream=None) -> None:
        self.stream = stream or sys.stderr
        self._live = _live_capable(self.stream)
        self._active = False
        self._last_draw = 0.0
        self._last_plain_pct = -1
        self._min_interval = 0.12

    def show(self, text: str, *, pct: float | None = None, force: bool = False) -> None:
        if self._live:
            now = time.monotonic()
            if not force and pct is not None:
                if pct < 1.0 and (now - self._last_draw) < self._min_interval:
                    return
                if pct < 1.0 and self._last_plain_pct >= 0:
                    if int(pct * 100) == int(self._last_plain_pct * 100):
                        return
            self._last_draw = now
            if pct is not None:
                self._last_plain_pct = pct
            self.stream.write(_CLEAR + text)
            self.stream.flush()
            self._active = True
            return

        if force or pct is None or pct >= 1.0 or pct == 0.0:
            self.stream.write(text + "\n")
            self.stream.flush()
            return

        step = int(pct * 4)  # 0%, 25%, 50%, 75%
        if step > self._last_plain_pct:
            self._last_plain_pct = step
            self.stream.write(text + "\n")
            self.stream.flush()

    def clear(self, final: str | None = None) -> None:
        if self._active and self._live:
            self.stream.write(_CLEAR)
            self._active = False
        if final:
            self.stream.write(final + "\n")
        self.stream.flush()
        self._last_plain_pct = -1


def visible_len(text: str) -> int:
    return len(_ANSI_RE.sub("", text))


def _pad_cell(cell: str, width: int, align: str) -> str:
    pad = max(0, width - visible_len(cell))
    if align == ">":
        return " " * pad + cell
    if align == "^":
        left = pad // 2
        return " " * left + cell + " " * (pad - left)
    return cell + " " * pad


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
    """Count-based progress for scan/preview operations."""

    def __init__(self, total: int, label: str = "Working") -> None:
        self.total = max(total, 1)
        self.label = label
        self.current = 0
        self._item = ""
        self._line = LiveLine()

    def update(self, current: int, item: str = "") -> None:
        self.current = current
        self._item = item
        pct = current / self.total
        short = (item[:30] + "…") if len(item) > 31 else item
        text = f"{self.label} {_bar(pct)} {current}/{self.total}"
        if short:
            text += f"  {short}"
        self._line.show(text, pct=pct, force=(current == self.total))

    def close(self, done_msg: str = "") -> None:
        self._line.clear(done_msg or None)


class TransferProgress:
    """Byte-level transfer progress for archive/restore operations."""

    def __init__(
        self,
        label: str = "Archiving",
        *,
        item: str = "",
        batch_current: int | None = None,
        batch_total: int | None = None,
    ) -> None:
        self.label = label
        self.item = item
        self.batch_current = batch_current
        self.batch_total = batch_total
        self.copied = 0
        self.total = 1
        self._file = ""
        self._line = LiveLine()

    def set_item(self, name: str) -> None:
        self.item = name
        self.copied = 0
        self.total = 1
        self._file = ""
        self._line._last_plain_pct = -1

    def set_batch(self, current: int, total: int) -> None:
        self.batch_current = current
        self.batch_total = total

    def update(self, copied: int, total: int, current_file: str = "") -> None:
        self.copied = copied
        self.total = max(total, 1)
        self._file = current_file
        pct = min(1.0, copied / self.total)
        force = current_file in {"done", "moving"} or pct >= 1.0 or copied == 0
        self._line.show(self._format_line(), pct=pct, force=force)

    def _prefix(self) -> str:
        parts = [self.label]
        if self.batch_current is not None and self.batch_total is not None:
            parts.append(f"({self.batch_current}/{self.batch_total})")
        if self.item:
            parts.append(self.item)
        return " ".join(parts)

    def _format_line(self) -> str:
        pct = min(1.0, self.copied / self.total)
        size = f"{format_bytes(self.copied)} / {format_bytes(self.total)}"
        text = f"{self._prefix()}  {_bar(pct)}  {size}"
        if self._file and self._file not in {"done", "starting", "moving"}:
            name = Path(self._file).name
            if len(name) > 28:
                name = name[:25] + "..."
            text += f"  {name}"
        return text

    def close(self) -> None:
        self._line.clear()


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


def print_table(
    headers: list[str],
    rows: list[list[str]],
    aligns: list[str] | None = None,
    *,
    gap: int = 2,
) -> None:
    aligns = aligns or ["<"] * len(headers)
    widths = [visible_len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            if i < len(widths):
                widths[i] = max(widths[i], visible_len(cell))

    sep = " " * gap

    print(sep.join(_pad_cell(bold(h), widths[i], aligns[i]) for i, h in enumerate(headers)))
    print(dim(sep.join("─" * w for w in widths)))
    for row in rows:
        print(sep.join(
            _pad_cell(cell, widths[i], aligns[i])
            for i, cell in enumerate(row)
            if i < len(widths)
        ))


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