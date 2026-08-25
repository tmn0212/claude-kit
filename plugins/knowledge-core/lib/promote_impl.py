"""Move a script out of the scratch directory and into the real harness.

This exists because of the economics, not the mechanics. Promoting a useful
throwaway script properly used to cost fifteen minutes - move it, make it
executable, add the success signal, register it, get it linted - and doing
nothing cost zero. So nothing is what happened, and the next session rewrote
the script. Making the good path one command is the whole fix.

`PROMOTE OK` on stdout is the success signal.
"""

from __future__ import annotations

import argparse
import datetime
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

from kit_config import Config, rel

SHEBANGS = {".py": "#!/usr/bin/env python3", ".sh": "#!/usr/bin/env bash"}


def git_mv(src: Path, dest: Path, root: Path) -> bool:
    """Prefer `git mv` so history follows the file. Fall back to a plain move."""
    try:
        subprocess.run(
            ["git", "-C", str(root), "mv", str(src), str(dest)],
            check=True,
            capture_output=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, OSError):
        shutil.move(str(src), str(dest))
        return False


def make_executable(path: Path) -> None:
    """A no-op on Windows, which has no executable bit, and that is fine."""
    try:
        mode = path.stat().st_mode
        path.chmod(mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    except OSError:
        pass


def ensure_shebang(path: Path) -> bool:
    """Without one, an executable script depends on how it happens to be invoked."""
    suffix = path.suffix
    if suffix not in SHEBANGS:
        return False
    # Bytes, not text. Reading with errors="replace" and writing the result back
    # turns every undecodable byte into U+FFFD permanently, and the move has
    # already happened, so there is no original left to recover from.
    raw = path.read_bytes()
    if raw.startswith(b"#!"):
        return False
    path.write_bytes(SHEBANGS[suffix].encode("utf-8") + b"\n" + raw)
    return True


def register(doc: Path, name: str, dest_dir: str) -> bool:
    """Add a row so one document stays the single list of the harness."""
    if not doc.is_file():
        return False
    text = doc.read_text(encoding="utf-8", errors="replace")
    if f"{dest_dir}/{name}" in text:
        return False
    today = datetime.date.today().isoformat()
    row = f"| `{dest_dir}/{name}` | TODO: one line on what it does. Promoted {today}. |"
    lines = text.splitlines()
    prefix = f"| `{dest_dir}/"
    last = None
    for index, line in enumerate(lines):
        if line.startswith(prefix):
            last = index
    if last is None:
        return False
    lines.insert(last + 1, row)
    doc.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return True


def shellcheck(path: Path) -> str | None:
    if path.suffix != ".sh" or shutil.which("shellcheck") is None:
        return None
    result = subprocess.run(
        ["shellcheck", "-S", "warning", str(path)], capture_output=True, text=True
    )
    if result.returncode == 0:
        return "  shellcheck: clean"
    return (result.stdout or result.stderr).rstrip() + "\n  shellcheck: fix the above"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="promote",
        description="Promote a scratch script into the project's tool directory.",
    )
    parser.add_argument("source", help="a bare name, a scratch-relative path, or a full path")
    parser.add_argument("dest_name", nargs="?", help="rename it on the way in")
    args = parser.parse_args(argv)

    cfg = Config.load()
    root = cfg.root
    scratch = cfg.path("promote.scratch")
    dest_dir = cfg.path("promote.dest")
    dest_label = str(cfg.get("promote.dest"))

    # Accept a bare name, a scratch-relative path, or a full path.
    candidates = [Path(args.source), root / args.source, scratch / args.source]
    src = next((p for p in candidates if p.is_file()), None)
    if src is None:
        sys.stderr.write(
            f"promote: no such file: {args.source} "
            f"(looked in ./, {rel(root, root) or '.'} and {rel(scratch, root)})\n"
        )
        return 1
    src = src.resolve()

    name = args.dest_name or src.name
    dest = dest_dir / name
    if dest.exists():
        sys.stderr.write(f"promote: {rel(dest, root)} already exists, pick another name\n")
        return 1
    dest_dir.mkdir(parents=True, exist_ok=True)

    if dest.suffix not in SHEBANGS:
        print(f"promote: note, '{name}' has no .sh or .py suffix; linters glob by extension")

    tracked = git_mv(src, dest, root)
    make_executable(dest)
    if ensure_shebang(dest):
        print("  added a shebang (there was none)")

    registry = cfg.get("promote.registry")
    registered = bool(registry) and register(root / registry, name, dest_label)
    if registered:
        print(f"  added a row to {registry}, replace the TODO with a real description")

    print()
    print(f"promoted: {rel(src, root)} -> {rel(dest, root)}" + ("" if tracked else "  (not tracked by git)"))
    print()
    print("Before committing, three things this script cannot do for you:")
    if registered:
        print(f"  1. Describe it in {registry} (the row is there, the description is TODO).")
    elif registry:
        print(f"  1. Add it to {registry} yourself: no table row there matched")
        print(f"     `| \u0060{dest_label}/...\u0060 |`, so there was nowhere to insert one.")
    else:
        print("  1. Describe it wherever this project lists its tools.")
    print("  2. Print a success signal. Every tool here ends with a greppable line")
    print("     (ADR OK, KB OK, PROMOTE OK). That is what makes it safe to drive from")
    print("     an agent session: grep the line, never trust exit code 0.")
    print("  3. Say why it exists in a header comment. In six weeks that is the only")
    print("     thing that will stop it being deleted as mystery scaffolding.")
    print()
    note = shellcheck(dest)
    if note:
        print(note)
    print("PROMOTE OK")
    return 0
