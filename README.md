# storageclean

CLI to free space on a cramped Mac by archiving dormant coding projects to an external SSD and cleaning rebuildable caches (`node_modules`, `.next`, etc.).

## Install

```bash
cd ~/Documents/coding/storageclean
pip3 install -e .   # installs both `storageclean` and `sc`

# or without pip, add bin/ to PATH:
export PATH="$HOME/Documents/coding/storageclean/bin:$PATH"
```

## Quick start

`sc` is a short alias for `storageclean`.

```bash
storageclean status          # disk usage overview
sc status                    # same thing
storageclean scan            # see all projects, sizes, caches, dormant status
storageclean scan --sync     # scan + update registry

storageclean pin draftanakitb-web   # keep active project local
storageclean archive RekSTI         # move one project to SSD
storageclean archive --dormant      # batch: archive all dormant projects
storageclean archive --dormant --dry-run
storageclean restore RekSTI         # bring back to internal disk

storageclean clean --dry-run        # preview cache cleanup across all projects
storageclean clean --dormant        # only clean caches in inactive projects
storageclean clean -p t3code        # clean one project
storageclean clean --only node_modules --only .next
storageclean clean --global --dry-run  # ~/.npm, ~/.cache, etc.
```

## How archiving works

1. Moves `~/Documents/coding/my-app` → `/Volumes/Data's Arqila/coding/my-app`
2. Creates a symlink at the original path
3. Tools (git, VS Code, terminal) still work when the SSD is plugged in

## Config

Stored at `~/.config/storageclean/config.json`. Override paths:

```bash
storageclean config --workspace ~/Documents/coding --archive "/Volumes/Data's Arqila/coding" --dormant-days 30
```

## Cache targets

`node_modules`, `.next`, `dist`, `build`, `.turbo`, `.cache`, `.venv`, `venv`, `__pycache__`, `.pytest_cache`, `coverage`, `target`, and more.