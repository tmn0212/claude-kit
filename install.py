#!/usr/bin/env python3
"""Install the one thing a plugin cannot ship: the user-level CLAUDE.md.

Everything else in claude-kit is a plugin, and plugins install themselves
through the marketplace. Claude Code explicitly does not let a plugin write a
`CLAUDE.md`, so this covers that gap and nothing else.

    python3 install.py                 copy CLAUDE.md, then print what to run
    python3 install.py --style         also set outputStyle to "Answer first"
    python3 install.py --project PATH  also drop a claude-kit.toml in a project
    python3 install.py --dry-run       say what it would do, change nothing

Anything it overwrites is backed up beside the original first. It never touches
credentials, and it never edits `~/.claude.json`.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import shutil
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MARKETPLACE = "tmn0212/claude-kit"
PLUGINS = ["knowledge-core", "session-economics", "writing", "agent-tiers"]


def claude_dir() -> Path:
    """`~/.claude`, or wherever CLAUDE_CONFIG_DIR points.

    On Windows `~` is `%USERPROFILE%`, which `Path.home()` already resolves.
    """
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(override) if override else Path.home() / ".claude"


def backup(path: Path, dry: bool) -> Path | None:
    if not path.exists():
        return None
    stamp = datetime.date.today().isoformat()
    target = path.with_name(f"{path.name}.bak-{stamp}")
    index = 2
    while target.exists():
        target = path.with_name(f"{path.name}.bak-{stamp}-{index}")
        index += 1
    if not dry:
        shutil.copyfile(path, target)
    return target


def install_claude_md(root: Path, dry: bool) -> None:
    source = HERE / "templates" / "CLAUDE.md"
    target = root / "CLAUDE.md"
    if not source.is_file():
        print(f"  skipped: {source} is missing")
        return
    if target.exists() and target.read_text(encoding="utf-8") == source.read_text(encoding="utf-8"):
        print(f"  {target} already matches the template")
        return
    saved = backup(target, dry)
    if saved:
        print(f"  backed up existing CLAUDE.md to {saved.name}")
    if not dry:
        root.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    print(f"  {'would write' if dry else 'wrote'} {target}")
    if saved:
        print("  your old one is not merged. Read the backup and fold in what you want.")


def set_output_style(root: Path, dry: bool) -> None:
    """Set `outputStyle`, leaving every other key alone.

    Claude Code rewrites this file itself, and there is an open upstream report
    of it being replaced wholesale, so the backup here is worth keeping.
    """
    path = root / "settings.json"
    data: dict = {}
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print(f"  {path} is not valid JSON; leaving it alone")
            return
    if data.get("outputStyle") == "Answer first":
        print('  outputStyle is already "Answer first"')
        return
    saved = backup(path, dry)
    if saved:
        print(f"  backed up settings.json to {saved.name}")
    data["outputStyle"] = "Answer first"
    if not dry:
        root.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"  {'would set' if dry else 'set'} outputStyle to \"Answer first\"")
    print("  it takes effect in a new session, or after /clear")


def install_project_config(project: Path, dry: bool) -> None:
    source = HERE / "templates" / "claude-kit.toml"
    target = project / "claude-kit.toml"
    if target.exists():
        print(f"  {target} already exists, leaving it alone")
        return
    if not dry:
        shutil.copyfile(source, target)
    print(f"  {'would write' if dry else 'wrote'} {target}")
    print("  edit the source directories in it before running `kb build`")


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="install.py", description="Install the user-level CLAUDE.md that plugins cannot ship."
    )
    parser.add_argument("--style", action="store_true", help='set outputStyle to "Answer first"')
    parser.add_argument("--project", metavar="PATH", help="also write a claude-kit.toml there")
    parser.add_argument("--dry-run", action="store_true", help="say what it would do")
    args = parser.parse_args()

    root = claude_dir()
    dry = args.dry_run
    print(f"claude-kit installer{'  (dry run)' if dry else ''}")
    print(f"config directory: {root}")
    print()

    print("CLAUDE.md")
    install_claude_md(root, dry)

    if args.style:
        print("\noutput style")
        set_output_style(root, dry)

    if args.project:
        project = Path(args.project).expanduser().resolve()
        print(f"\nproject config in {project}")
        if not project.is_dir():
            print("  no such directory")
        else:
            install_project_config(project, dry)

    print("\nNow install the plugins. Add the marketplace once, then take what you want:")
    print()
    interpreter = "py" if os.name == "nt" else "python3"
    print(f"  claude plugin marketplace add {MARKETPLACE}")
    for name in PLUGINS:
        print(f"  claude plugin install {name}@claude-kit --scope user --config python={interpreter}")
    print()
    print(f"The `python` option is set to `{interpreter}` above because that is what")
    print("this platform has. Change it later with `/plugin configure <plugin>`.")
    print()
    print("INSTALL OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
