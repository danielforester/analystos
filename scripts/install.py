#!/usr/bin/env python3
"""
AnalystOS install/upgrade script.

Copies skills and hooks from install/ into ~/.claude/ and merges the
hooks block into ~/.claude/settings.json. Safe to re-run for upgrades.

Usage:
    python scripts/install.py                          # Install or upgrade global components
    python scripts/install.py --project-dir PATH       # Also copy connector scripts into a project
    python scripts/install.py --dry-run                # Preview changes without writing
    python scripts/install.py --project-dir PATH --dry-run

Connector scripts (athena_connect.py, oracle_connect.py, snowflake_connect.py)
are project-local — skills call them via relative paths (e.g. python scripts/oracle_connect.py).
Without --project-dir, they are NOT installed and existing project copies will not be updated.
"""

import argparse
import json
import shutil
import stat
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).parent.parent
INSTALL_DIR = REPO_ROOT / "install"
CLAUDE_DIR = Path.home() / ".claude"

# On Windows use "python"; on macOS/Linux use "python3"
PYTHON_CMD = "python" if sys.platform == "win32" else "python3"
NEED_CHMOD = sys.platform != "win32"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _echo(label: str, msg: str, dry: bool = False) -> None:
    prefix = "[dry-run] " if dry else ""
    print(f"  {prefix}{label:10s} {msg}")


def _collect_hook_commands(settings: dict) -> set:
    """Return the set of all hook command strings registered in settings."""
    commands = set()
    for event in ("PreToolUse", "PostToolUse"):
        for entry in settings.get("hooks", {}).get(event, []):
            for h in entry.get("hooks", []):
                if "command" in h:
                    commands.add(h["command"])
    return commands


def _rewrite_python_cmd(command: str) -> str:
    """Replace 'python ' prefix with the platform-correct executable."""
    if command.startswith("python "):
        return PYTHON_CMD + command[len("python"):]
    return command


# ---------------------------------------------------------------------------
# Steps
# ---------------------------------------------------------------------------

def install_skills(dry: bool) -> None:
    src = INSTALL_DIR / "skills"
    dst = CLAUDE_DIR / "skills"
    if not src.is_dir():
        print(f"  WARNING  skills source not found: {src}")
        return
    if not dry:
        dst.mkdir(parents=True, exist_ok=True)
    for skill_dir in sorted(src.iterdir()):
        if not skill_dir.is_dir():
            continue
        target = dst / skill_dir.name
        action = "upgrade" if target.exists() else "install"
        _echo(action, f"skill  {skill_dir.name}", dry)
        if not dry:
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(skill_dir, target)


def install_hooks(dry: bool) -> None:
    src = INSTALL_DIR / "hooks"
    dst = CLAUDE_DIR / "hooks"
    if not src.is_dir():
        print(f"  WARNING  hooks source not found: {src}")
        return
    if not dry:
        dst.mkdir(parents=True, exist_ok=True)
    for hook_file in sorted(src.glob("*.py")):
        target = dst / hook_file.name
        action = "upgrade" if target.exists() else "install"
        _echo(action, f"hook   {hook_file.name}", dry)
        if not dry:
            shutil.copy2(hook_file, target)
            if NEED_CHMOD:
                target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def merge_settings(dry: bool) -> None:
    template_path = INSTALL_DIR / "settings.json"
    target_path = CLAUDE_DIR / "settings.json"

    if not template_path.exists():
        print(f"  WARNING  settings template not found: {template_path}")
        return

    with open(template_path, encoding="utf-8") as f:
        template = json.load(f)

    existing: dict = {}
    if target_path.exists():
        with open(target_path, encoding="utf-8") as f:
            existing = json.load(f)

    existing_cmds = _collect_hook_commands(existing)
    added = []

    for event in ("PreToolUse", "PostToolUse"):
        template_entries = template.get("hooks", {}).get(event, [])
        if not template_entries:
            continue
        for entry in template_entries:
            # Build a clean copy, rewriting the command string and dropping _comment/_instructions
            clean_hooks = []
            for h in entry.get("hooks", []):
                clean_hooks.append({k: (_rewrite_python_cmd(v) if k == "command" else v)
                                    for k, v in h.items()})
            clean_entry = {k: v for k, v in entry.items()
                           if not k.startswith("_")}
            clean_entry["hooks"] = clean_hooks

            cmd = clean_hooks[0].get("command", "") if clean_hooks else ""
            if cmd in existing_cmds:
                _echo("skip", f"{event} hook already registered: {cmd}", dry)
                continue

            _echo("add", f"{event} hook: {cmd}", dry)
            added.append((event, clean_entry))
            if not dry:
                existing.setdefault("hooks", {}).setdefault(event, []).append(clean_entry)

    if not added:
        return

    if not dry:
        CLAUDE_DIR.mkdir(parents=True, exist_ok=True)
        with open(target_path, "w", encoding="utf-8") as f:
            json.dump(existing, f, indent=2)
        print(f"  written   {target_path}")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def install_connectors(project_dir: Path, dry: bool) -> None:
    """Copy connector scripts and the connectors package into a project's scripts/ directory."""
    scripts_src = REPO_ROOT / "scripts"
    scripts_dst = project_dir / "scripts"

    if not dry:
        scripts_dst.mkdir(parents=True, exist_ok=True)

    # Copy the connectors package (common.py, athena.py, oracle.py, snowflake.py, tests/)
    connectors_src = scripts_src / "connectors"
    connectors_dst = scripts_dst / "connectors"
    if connectors_src.is_dir():
        action = "upgrade" if connectors_dst.exists() else "install"
        _echo(action, f"scripts/connectors/  -> {connectors_dst}", dry)
        if not dry:
            if connectors_dst.exists():
                shutil.rmtree(connectors_dst)
            shutil.copytree(connectors_src, connectors_dst)
    else:
        print(f"  WARNING  connectors source not found: {connectors_src}")

    # Copy top-level *_connect.py CLI wrappers
    wrappers = sorted(scripts_src.glob("*_connect.py"))
    if not wrappers:
        print(f"  WARNING  no *_connect.py wrappers found in {scripts_src}")
    for wrapper in wrappers:
        target = scripts_dst / wrapper.name
        action = "upgrade" if target.exists() else "install"
        _echo(action, f"scripts/{wrapper.name}", dry)
        if not dry:
            shutil.copy2(wrapper, target)


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="install",
        description="Install or upgrade AnalystOS skills, hooks, and connector scripts.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview changes without writing any files",
    )
    parser.add_argument(
        "--project-dir",
        metavar="PATH",
        type=Path,
        default=None,
        help=(
            "Copy connector scripts into this project directory. "
            "Without this flag, connectors are not installed and existing project copies "
            "will not receive updates."
        ),
    )
    args = parser.parse_args()
    dry = args.dry_run
    project_dir = args.project_dir

    if dry:
        print("DRY RUN — no files will be written\n")

    print(f"Platform : {sys.platform}  (python cmd in hooks: '{PYTHON_CMD}')")
    print(f"Source   : {INSTALL_DIR}")
    print(f"Target   : {CLAUDE_DIR}\n")

    print("Skills:")
    install_skills(dry)

    print("\nHooks:")
    install_hooks(dry)

    print("\nsettings.json:")
    merge_settings(dry)

    if project_dir is not None:
        print(f"\nConnector scripts -> {project_dir}:")
        install_connectors(project_dir, dry)
    else:
        print(
            "\nNOTE: --project-dir not specified. Connector scripts "
            "(athena_connect.py, oracle_connect.py, snowflake_connect.py) "
            "were NOT installed.\n"
            "      Existing projects will not receive connector updates until you run:\n"
            f"      python scripts/install.py --project-dir /path/to/your/project"
        )

    print("\nDone." if not dry else "\nDry run complete — rerun without --dry-run to apply.")


if __name__ == "__main__":
    main()
