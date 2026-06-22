from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Callable

if TYPE_CHECKING:
    from .ui import TransferProgress


def _same_filesystem(a: Path, b: Path) -> bool:
    try:
        return os.stat(a).st_dev == os.stat(b.parent if not b.exists() else b).st_dev
    except OSError:
        return False


def _iter_files(root: Path) -> list[tuple[Path, int]]:
    files: list[tuple[Path, int]] = []
    for path in root.rglob("*"):
        if path.is_file():
            try:
                files.append((path, path.stat().st_size))
            except OSError:
                pass
    return files


def move_tree_with_progress(
    src: Path,
    dst: Path,
    *,
    progress: TransferProgress | None = None,
    on_bytes: Callable[[int, int, str], None] | None = None,
) -> int:
    """Move a directory tree to dst, reporting byte-level progress for cross-volume copies."""
    if dst.exists():
        raise FileExistsError(dst)

    total = sum(size for _, size in _iter_files(src))
    if total == 0:
        try:
            src.rename(dst)
        except OSError:
            dst.mkdir(parents=True, exist_ok=True)
            shutil.rmtree(src)
        _report(progress, on_bytes, 0, 0, "done")
        return 0

    def report(copied: int, rel: str, *, force: bool = False) -> None:
        _report(progress, on_bytes, copied, total, rel, force=force)

    parent = dst.parent
    parent.mkdir(parents=True, exist_ok=True)

    if _same_filesystem(src, parent):
        report(total, "moving")
        src.rename(dst)
        report(total, "done")
        return total

    copied = 0
    report(0, "", force=True)
    last_reported = 0
    min_step = max(256 * 1024, total // 200)  # ~200 updates max per project

    for file_path, size in _iter_files(src):
        rel = file_path.relative_to(src)
        target = dst / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, target)
        copied += size
        if copied - last_reported >= min_step or copied == total:
            report(copied, str(rel))
            last_reported = copied

    shutil.rmtree(src)
    report(total, "done", force=True)
    return total


def _report(
    progress: TransferProgress | None,
    on_bytes: Callable[[int, int, str], None] | None,
    copied: int,
    total: int,
    rel: str,
    *,
    force: bool = False,
) -> None:
    if progress is not None:
        if force and rel == "done":
            progress.update(copied, total, "done")
        elif force and rel in {"", "moving"}:
            progress.update(copied, total, rel or "moving")
        else:
            progress.update(copied, total, rel)
    if on_bytes is not None:
        on_bytes(copied, total, rel)