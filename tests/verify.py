#!/usr/bin/env python3
"""Everything, in one command, with one signal.

    python3 tests/verify.py            run every stage
    python3 tests/verify.py --fast     skip the stages that need the network
    python3 tests/verify.py --list     what it would run

`VERIFY OK` on stdout is the pass condition. Grep for the line, not exit 0.

## Why this exists

Bugs kept surviving here, and the pattern behind them was always the same: the
work was declared done, and only then verified. Three of them were found by the
verification step that ran AFTER the announcement, including one that left the
knowledge index permanently empty in every new project.

So the fix is not more tests, it is one command cheap enough to run BEFORE
saying anything. Each stage is independent and prints its own signal, so a
failure names itself rather than requiring a bisect.

## The stages

| Stage | What it would catch |
|---|---|
| json | A manifest that will not parse, before a plugin ever loads |
| validate | A manifest the harness rejects, such as a userConfig with no title |
| cases | A guard that denies working commands, or misses the shape it exists to catch |
| smoke | A tool that half-works in a project that is not this one |
| prose | A document that drifted past the readability thresholds |
| shebang | A launcher with CRLF, which is unrunnable on POSIX and the top Windows failure |
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN_NAMES = ["knowledge-core", "session-economics", "writing", "agent-tiers", "claim-gate"]

# A stage returns True, False, or SKIP. The three used to be two, and a skipped
# dependency returned True: on any machine without the claude CLI the entire
# manifest-schema stage was a no-op that printed `ok`.
SKIP = "skip"

# Floors, so an empty collection cannot pass. Every one of these counted zero
# and reported ok at some point: no manifests, no documents, no launchers.
MIN_MANIFESTS = 9
MIN_DOCS = 20
MIN_LAUNCHERS = 8


class Stage:
    def __init__(self, name: str, needs_network: bool = False):
        self.name = name
        self.needs_network = needs_network


def stage_json() -> tuple[bool, str]:
    files = [p for p in ROOT.rglob("*.json") if ".venv" not in p.parts and ".git" not in p.parts]
    for path in files:
        try:
            json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            return False, f"{path.relative_to(ROOT)}: {exc}"
    if len(files) < MIN_MANIFESTS:
        return False, f"only {len(files)} manifests found, expected at least {MIN_MANIFESTS}"
    return True, f"{len(files)} manifests parse"


def stage_validate() -> tuple[bool, str]:
    claude = shutil.which("claude") or str(Path.home() / ".local" / "bin" / "claude")
    if not Path(claude).exists():
        return SKIP, "the claude CLI is not on this machine"
    targets = [ROOT] + [ROOT / "plugins" / name for name in PLUGIN_NAMES]
    for target in targets:
        result = subprocess.run(
            [claude, "plugin", "validate", str(target)],
            capture_output=True, text=True, timeout=120,
        )
        if "Validation passed" not in result.stdout:
            return False, f"{target.name}: {(result.stdout + result.stderr).strip()[:200]}"
    return True, f"{len(targets)} manifests accepted by the harness"


def stage_cases() -> tuple[bool, str]:
    result = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "cases.py")],
        capture_output=True, text=True, timeout=600,
    )
    if "CASES OK" not in result.stdout:
        return False, (result.stdout + result.stderr).strip()[-400:]
    return True, result.stdout.strip().splitlines()[-2]


def stage_smoke() -> tuple[bool, str]:
    result = subprocess.run(
        [sys.executable, str(ROOT / "tests" / "smoke.py")],
        capture_output=True, text=True, timeout=900,
    )
    if "SMOKE OK" not in result.stdout:
        tail = [ln for ln in result.stdout.splitlines() if "FAIL" in ln]
        return False, " | ".join(tail[:4]) or (result.stdout + result.stderr)[-400:]
    return True, result.stdout.strip().splitlines()[-2]


def stage_prose() -> tuple[bool, str]:
    prose = ROOT / "plugins" / "writing" / "bin" / "prose"
    docs = sorted(
        p for p in ROOT.rglob("*.md")
        if ".venv" not in p.parts and ".git" not in p.parts and "vale" not in p.parts
    )
    probe = subprocess.run(
        [sys.executable, str(prose), "score", "-"],
        input="A short line.", capture_output=True, text=True, timeout=120,
    )
    if "not installed" in probe.stderr:
        return SKIP, "run `prose --setup` to enable"
    bad = []
    for path in docs:
        result = subprocess.run(
            [sys.executable, str(prose), "check", "--doc", str(path)],
            capture_output=True, text=True, timeout=120,
        )
        if "PROSE OK" not in result.stdout:
            first = next(
                (ln.strip() for ln in result.stdout.splitlines() if "FAIL" in ln and ":" in ln),
                "",
            )
            bad.append(f"{path.relative_to(ROOT)} ({first})")
    if bad:
        return False, "; ".join(bad[:4])
    if len(docs) < MIN_DOCS:
        return False, f"only {len(docs)} documents found, expected at least {MIN_DOCS}"
    return True, f"{len(docs)} documents within the thresholds"


def stage_shebang() -> tuple[bool, str]:
    """CRLF in a launcher is unrunnable on POSIX and the top Windows failure."""
    bad = []
    for path in sorted((ROOT / "plugins").rglob("*")):
        if not path.is_file() or path.suffix in (".cmd", ".ps1"):
            continue
        try:
            head = path.read_bytes()[:200]
        except OSError:
            continue
        if head.startswith(b"#!") and b"\r\n" in head:
            bad.append(str(path.relative_to(ROOT)))
    # And the reverse: a .cmd without CRLF is not reliably parsed by cmd.exe.
    for path in sorted((ROOT / "plugins").rglob("*.cmd")):
        body = path.read_bytes()
        if b"\r\n" not in body:
            bad.append(f"{path.relative_to(ROOT)} (no CRLF)")
        if b"for %%I in" not in body:
            bad.append(f"{path.relative_to(ROOT)} (needs the batch-file %%I form)")
    if bad:
        return False, "; ".join(bad[:4])
    # A launcher that LOST its shebang is invisible to a loop that only inspects
    # files which already have one, so count them rather than trusting the scan.
    # `.venv/bin` matches "a bin directory" too, and a virtualenv's contents are
    # not ours to police. It should not be here at all: the prose venv lives in
    # the user cache so it survives a plugin update.
    posix = [
        p for p in (ROOT / "plugins").rglob("*")
        if p.is_file() and p.parent.name == "bin" and p.suffix != ".cmd"
        and ".venv" not in p.parts
    ]
    without = [str(p.relative_to(ROOT)) for p in posix if not p.read_bytes().startswith(b"#!")]
    if without:
        return False, "no shebang: " + "; ".join(without[:4])
    if len(posix) < MIN_LAUNCHERS:
        return False, f"only {len(posix)} launchers found, expected at least {MIN_LAUNCHERS}"
    return True, f"{len(posix)} launchers, right shebangs and line endings"


STAGES = [
    ("json", stage_json, False),
    ("validate", stage_validate, True),
    ("shebang", stage_shebang, False),
    ("cases", stage_cases, False),
    ("smoke", stage_smoke, False),
    ("prose", stage_prose, False),
]


def main() -> int:
    parser = argparse.ArgumentParser(prog="verify.py", description=__doc__.split("\n")[0])
    parser.add_argument("--fast", action="store_true", help="skip stages needing the network")
    parser.add_argument("--list", action="store_true", help="what it would run")
    args = parser.parse_args()

    if args.list:
        for name, _fn, network in STAGES:
            print(f"  {name:<10} {'(network)' if network else ''}")
        return 0

    failed: list[str] = []
    skipped: list[str] = []
    for name, run, network in STAGES:
        if args.fast and network:
            print(f"  {'SKIP':<6} {name:<10}   --fast")
            skipped.append(name)
            continue
        started = time.monotonic()
        try:
            ok, detail = run()
        except Exception as exc:  # a stage that crashes is a failed stage
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        elapsed = time.monotonic() - started
        state = "SKIP" if ok == SKIP else ("ok" if ok else "FAIL")
        print(f"  {state:<6} {name:<10} {elapsed:>5.1f}s  {detail}")
        if ok == SKIP:
            skipped.append(name)
        elif not ok:
            failed.append(name)

    print()
    if failed:
        print(f"VERIFY FAILED: {', '.join(failed)}")
        return 1
    if skipped:
        # Not a failure, but not a clean bill either. Saying so is the whole
        # point: a stage that did no work must not read as a stage that passed.
        print(f"VERIFY OK, WITH SKIPS: {', '.join(skipped)}")
        return 0
    print("VERIFY OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
