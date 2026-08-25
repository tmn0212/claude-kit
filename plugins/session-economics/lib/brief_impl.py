"""Where the project stands, in about twenty lines, at the top of every session.

This is the most expensive text in the harness: it is paid on every session,
forever, and it sits at the very front of the context window where every later
request re-reads it. So it is capped, and every line has to earn its place by
changing what the session would do next.

What earns a line: the git state, the decisions still open, the scratch backlog,
whether the knowledge index is behind, and where past sessions burned time.
What does not: anything the session can look up when it turns out to matter.

`BRIEF OK` on stdout is the success signal. The session-start hook prints this
only when it sees that line, so a half-finished brief is never injected.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

from kit_config import Config

CAP = 30


def git(root: Path, *args: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def find_sibling(name: str) -> Path | None:
    """Locate a tool in this plugin or in a sibling plugin's bin/.

    Plugins install as separate cache copies, so a hard relative path would
    break. Searching this plugin first and then its siblings finds `adr` when
    knowledge-core is installed, and skips the line cleanly when it is not.
    """
    here = Path(__file__).resolve().parent.parent
    candidates = [here / "bin" / name]
    if here.parent.is_dir():
        try:
            candidates += [p / "bin" / name for p in sorted(here.parent.iterdir()) if p.is_dir()]
        except OSError:
            pass
    return next((c for c in candidates if c.is_file()), None)


def call(root: Path, name: str, *args: str) -> str:
    tool = find_sibling(name)
    if tool is None:
        return ""
    try:
        result = subprocess.run(
            [sys.executable, str(tool), *args],
            capture_output=True,
            text=True,
            cwd=str(root),
            timeout=20,
        )
        return result.stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="brief", description="Where the project stands. Printed at session start."
    )
    parser.add_argument("--mark", action="store_true", help="record the head seen")
    args = parser.parse_args(argv)

    cfg = Config.load()
    root = cfg.root
    lines: list[str] = []

    lines.append(f"PROJECT  {cfg.name}")

    branch = git(root, "rev-parse", "--abbrev-ref", "HEAD")
    if branch:
        head = git(root, "log", "-1", "--format=%h %s")
        dirty = git(root, "status", "--porcelain")
        state = f"[{len(dirty.splitlines())} uncommitted]" if dirty else "[clean]"
        lines.append(f"  git    {branch} @ {head[:72]}  {state}")

    # Decisions still open. `proposed` IS the open-questions register, which is
    # why this is one command and not a separate list to maintain.
    decisions = call(root, "adr", "open")
    if "ADR OK" in decisions:
        rows = [
            ln
            for ln in decisions.splitlines()
            if ln[:4].isdigit() and "proposed" in ln
        ]
        accepted = call(root, "adr", "list", "--status", "accepted")
        n_accepted = len([ln for ln in accepted.splitlines() if ln[:4].isdigit()])
        lines.append(f"  adr    {n_accepted} accepted, {len(rows)} open")
        for row in rows[:6]:
            lines.append(f"           {row}")
        if len(rows) > 6:
            lines.append(f"           ... and {len(rows) - 6} more (adr open)")

    # Scratch backlog. A scratch directory that only grows is a directory nobody
    # trusts, so the count is surfaced where it will be seen.
    scratch = cfg.path("promote.scratch")
    if scratch.is_dir():
        pending = [p.name for p in sorted(scratch.iterdir()) if p.is_file()]
        if pending:
            shown = " ".join(pending[:8])
            more = f" ... +{len(pending) - 8}" if len(pending) > 8 else ""
            lines.append(f"  scratch {len(pending)} file(s) awaiting promote-or-delete: {shown}{more}")
            lines.append(f"           promote <file>   or   rm {cfg.get('promote.scratch')}/<file>")

    # Is the knowledge index behind its sources?
    stale = call(root, "kb", "stale")
    if "KB OK" in stale:
        summary = [ln for ln in stale.splitlines() if "differ from the index" in ln]
        if summary:
            lines.append(f"  kb     {summary[0].strip()}")

    # Where past sessions burned time.
    friction = call(root, "friction", "--brief", "--since", "14")
    for line in friction.splitlines():
        if line.strip() and "FRICTION OK" not in line:
            lines.append(f"  {line.strip()}")

    for line in lines[:CAP]:
        print(line)
    if len(lines) > CAP:
        print(f"  ... {len(lines) - CAP} line(s) trimmed to keep the brief cheap")

    if args.mark:
        try:
            marker = root / "log" / "brief"
            marker.mkdir(parents=True, exist_ok=True)
            (marker / "last-seen").write_text(git(root, "rev-parse", "HEAD"))
        except OSError:
            pass

    print("BRIEF OK")
    return 0
