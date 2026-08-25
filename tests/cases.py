#!/usr/bin/env python3
"""Run every case in tests/cases.toml.

Separate from smoke.py on purpose. smoke.py drives LIFECYCLES, which need
sequencing: build an index, then query it; create a record, then accept it. This
runs CASES, which are independent and want to be a table.

The split matters because of how bugs actually got through here. Each new guard
case meant writing a test function, and that friction is why cases were added
after a bug rather than before. A case is now one table entry, so the cheap
thing to do is add it first.

    python3 tests/cases.py              every case, about a second
    python3 tests/cases.py bash_guard   one group
    python3 tests/cases.py -v           print the reason for each case

`CASES OK` on stdout is the success signal.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGINS = ROOT / "plugins"
CASES = Path(__file__).resolve().parent / "cases.toml"

# Enough repeats to clear the default 25-line heredoc limit with room to spare.
PAD_REPEATS = 40

results: list[tuple[bool, str, str]] = []


def record(ok: bool, label: str, detail: str = "") -> None:
    results.append((ok, label, detail))


def body(case: dict) -> str:
    """A case's command, with `pad` repeated so a long one stays readable."""
    text = case.get("cmd", case.get("text", ""))
    if case.get("pad"):
        text += case["pad"] * PAD_REPEATS
    return text + case.get("tail", "")


def hook(path: Path, argv: list[str], payload: dict, cwd: Path) -> str:
    result = subprocess.run(
        [sys.executable, str(path), *argv],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        cwd=str(cwd),
        env={"PATH": "/usr/bin:/bin", "HOME": str(Path.home()), "CLAUDE_PROJECT_DIR": str(cwd)},
        timeout=30,
    )
    return result.stdout.strip()


def run_bash_guard(cases: dict, root: Path, verbose: bool) -> None:
    guard = PLUGINS / "session-economics" / "hooks" / "guard.py"
    for outcome in ("allow", "deny"):
        for case in cases.get(outcome, []):
            command = body(case)
            out = hook(guard, ["bash"], {"tool_input": {"command": command}}, root)
            ok = (out == "") if outcome == "allow" else ('"deny"' in out)
            label = f"bash_guard {outcome}: {case['why']}" if verbose else \
                f"bash_guard {outcome}: {command.splitlines()[0][:52]}"
            record(ok, label, out[:200])


def run_claim_gate(cases: dict, root: Path, verbose: bool) -> None:
    guard = PLUGINS / "claim-gate" / "hooks" / "claim_guard.py"
    transcript = root / "cases.jsonl"
    for outcome in ("allow", "deny"):
        for index, case in enumerate(cases.get(outcome, [])):
            transcript.write_text(
                json.dumps(
                    {"type": "assistant",
                     "message": {"content": [{"type": "text", "text": case["text"]}]}}
                )
                + "\n"
            )
            # A fresh session id per case: the gate fires once per session, so
            # reusing one would make every case after the first pass vacuously.
            out = hook(
                guard,
                [],
                {"transcript_path": str(transcript),
                 "session_id": f"{outcome}{index}",
                 "stop_hook_active": False},
                root,
            )
            ok = (out == "") if outcome == "allow" else ('"block"' in out)
            label = f"claim_gate {outcome}: {case['why']}" if verbose else \
                f"claim_gate {outcome}: {case['text'].splitlines()[0][:52]}"
            record(ok, label, out[:200])


def run_kb_query(cases: dict, root: Path, verbose: bool) -> None:
    kb = PLUGINS / "knowledge-core" / "bin" / "kb"
    for case in cases.get("ok", []):
        result = subprocess.run(
            [sys.executable, str(kb), "search", case["query"]],
            capture_output=True, text=True, cwd=str(root), timeout=60,
        )
        # Two expectations, because "found nothing" and "did not crash" are
        # different claims and the old check conflated them. It asserted only
        # `"bad query" not in stderr`, which a traceback also satisfies.
        clean = result.returncode == 0 and "Traceback" not in result.stderr
        if case.get("expect", "clean") == "hits":
            # `KB OK` prints only on a non-empty result, so it doubles as a
            # "found something" assertion.
            ok = clean and "KB OK" in result.stdout
            if ok and case.get("finds"):
                ok = case["finds"] in result.stdout
        else:
            # The whole of stdout is pinned. A syntax error or a traceback both
            # produce something other than exactly this.
            ok = clean and result.stdout.strip() == "no hits"
        label = f"kb_query: {case['why']}" if verbose else f"kb_query: {case['query']}"
        record(ok, label, (result.stderr or result.stdout)[:200])


def prepare(root: Path) -> None:
    """A project the cases can run against: a config, a document, an index."""
    (root / "docs").mkdir(parents=True, exist_ok=True)
    (root / "claude-kit.toml").write_text(
        '[project]\nname = "cases"\n\n'
        '[kb]\nsources = ["docs"]\nextensions = [".md"]\n\n'
        '[kb.aliases]\ndma = "direct memory access"\n\n'
        '[adr]\ndir = "docs/decisions"\n'
    )
    # The fixture has to contain something for every `expect = "hits"` case, or
    # the case degrades into "did not crash" without saying so.
    (root / "docs" / "note.md").write_text(
        "# Note\n\n"
        "## Zebra\n\n"
        "Zebras are striped, ratio 2:1, on v1.3 with allkeys-lru.\n\n"
        "## Parsing\n\n"
        "The C++ (draft) grammar and *args handling both need direct memory access.\n"
    )
    subprocess.run(["git", "init", "-q"], cwd=str(root), capture_output=True)
    subprocess.run(
        [sys.executable, str(PLUGINS / "knowledge-core" / "bin" / "kb"), "build"],
        cwd=str(root), capture_output=True, timeout=60,
    )


RUNNERS = {
    "bash_guard": run_bash_guard,
    "claim_gate": run_claim_gate,
    "kb_query": run_kb_query,
}

# The floor each group must clear. Without it, emptying the table or misspelling
# a group name prints CASES OK having run nothing, which is what a review
# reproduced. Raise a number here when you add cases; never lower one to make a
# run pass.
FLOORS = {
    "bash_guard": 34,
    "claim_gate": 15,
    "kb_query": 7,
}


def main() -> int:
    parser = argparse.ArgumentParser(prog="cases.py", description=__doc__.split("\n")[0])
    parser.add_argument("group", nargs="?", choices=sorted(RUNNERS), help="run one group")
    parser.add_argument("-v", "--verbose", action="store_true", help="print each case's reason")
    args = parser.parse_args()

    with open(CASES, "rb") as handle:
        tables = tomllib.load(handle)

    # A group in the table with no runner is a typo, and silence is how a typo
    # survives. Name it before running anything.
    unknown = sorted(set(tables) - set(RUNNERS))
    if unknown:
        print(f"CASES FAILED: no runner for {', '.join(unknown)} in cases.toml")
        return 1

    groups = [args.group] if args.group else sorted(RUNNERS)
    with tempfile.TemporaryDirectory(prefix="claude-kit-cases-") as workdir:
        root = Path(workdir)
        prepare(root)
        for name in groups:
            before = len(results)
            RUNNERS[name](tables.get(name, {}), root, args.verbose)
            ran = len(results) - before
            floor = FLOORS.get(name, 1)
            if ran < floor:
                record(False, f"{name}: ran {ran} cases, floor is {floor}",
                       "a group that runs fewer cases than it used to is a table "
                       "that lost entries, or a group name that no longer matches")

    failures = [r for r in results if not r[0]]
    for ok, label, detail in results:
        if ok and not args.verbose:
            continue
        print(f"  {'ok  ' if ok else 'FAIL'} {label}")
        if not ok and detail:
            print(f"         {detail.splitlines()[0][:150]}")

    total = len(results)
    if failures:
        print(f"\nCASES FAILED: {len(failures)} of {total}")
        return 1
    print(f"\n{total} cases passed")
    print("CASES OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
