from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_WORKSPACE = Path.home() / "Documents" / "coding"
DEFAULT_ARCHIVE = Path("/Volumes/Data's Arqila/coding")
CONFIG_DIR = Path.home() / ".config" / "storageclean"
CONFIG_FILE = CONFIG_DIR / "config.json"
REGISTRY_FILE = CONFIG_DIR / "registry.json"

# Directories safe to delete inside projects (rebuildable caches/artifacts).
CACHE_TARGETS = [
    "node_modules",
    ".next",
    "dist",
    "build",
    ".turbo",
    ".cache",
    ".parcel-cache",
    ".nuxt",
    ".output",
    ".svelte-kit",
    "coverage",
    ".pytest_cache",
    "__pycache__",
    ".venv",
    "venv",
    ".gradle",
    "target",  # Rust/Java
    ".angular",
    ".expo",
    "ios/build",
    "android/build",
    "android/.gradle",
]

DORMANT_DAYS_DEFAULT = 30

# Projects that must never be archived (this tool's own repo, etc.).
PROTECTED_PROJECTS = frozenset({"storageclean"})


def is_protected(name: str) -> bool:
    return name in PROTECTED_PROJECTS


@dataclass
class Config:
    workspace: str = str(DEFAULT_WORKSPACE)
    archive: str = str(DEFAULT_ARCHIVE)
    dormant_days: int = DORMANT_DAYS_DEFAULT
    pinned: list[str] = field(default_factory=list)

    @classmethod
    def load(cls) -> Config:
        if not CONFIG_FILE.exists():
            config = cls()
            config._ensure_protected_pinned()
            return config
        data = json.loads(CONFIG_FILE.read_text())
        config = cls(
            workspace=data.get("workspace", str(DEFAULT_WORKSPACE)),
            archive=data.get("archive", str(DEFAULT_ARCHIVE)),
            dormant_days=data.get("dormant_days", DORMANT_DAYS_DEFAULT),
            pinned=data.get("pinned", []),
        )
        config._ensure_protected_pinned()
        return config

    def _ensure_protected_pinned(self) -> None:
        changed = False
        for name in PROTECTED_PROJECTS:
            if name not in self.pinned:
                self.pinned.append(name)
                changed = True
        if changed and CONFIG_FILE.exists():
            self.save()

    def save(self) -> None:
        self._ensure_protected_pinned()
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(asdict(self), indent=2) + "\n")

    @property
    def workspace_path(self) -> Path:
        return Path(self.workspace).expanduser()

    @property
    def archive_path(self) -> Path:
        return Path(self.archive).expanduser()


@dataclass
class ProjectRecord:
    name: str
    status: str  # active | archived | symlinked
    size_bytes: int = 0
    last_activity: str | None = None
    last_git_commit: str | None = None
    archived_at: str | None = None
    pinned: bool = False
    cache_bytes: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class Registry:
    def __init__(self) -> None:
        self.projects: dict[str, ProjectRecord] = {}
        self._load()

    def _load(self) -> None:
        if not REGISTRY_FILE.exists():
            return
        data = json.loads(REGISTRY_FILE.read_text())
        for name, rec in data.get("projects", {}).items():
            self.projects[name] = ProjectRecord(**rec)

    def save(self) -> None:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        payload = {
            "projects": {k: v.to_dict() for k, v in self.projects.items()},
        }
        REGISTRY_FILE.write_text(json.dumps(payload, indent=2) + "\n")

    def get(self, name: str) -> ProjectRecord | None:
        return self.projects.get(name)

    def upsert(self, record: ProjectRecord) -> None:
        self.projects[record.name] = record
        self.save()


def archive_mounted(archive: Path) -> bool:
    volume = archive.parent
    return volume.exists() and os.path.ismount(str(volume))