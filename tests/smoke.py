#!/usr/bin/env python3
"""End-to-end smoke test: build a throwaway project and drive every tool.

This is the check that the kit works in a project that is not the one it was
extracted from. It creates a temporary directory, writes a config and a couple
of documents, then runs each tool and asserts on its success signal.

    python3 tests/smoke.py            run it
    python3 tests/smoke.py --keep     leave the temporary project behind

`SMOKE OK` on stdout is the success signal. Grep for the line, not exit code 0.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGINS = ROOT / "plugins"

FAILURES: list[str] = []
CHECKS = 0


def bin_path(plugin: str, name: str) -> Path:
    return PLUGINS / plugin / "bin" / name


def run(tool: Path, args: list[str], cwd: Path, stdin: str | None = None):
    return subprocess.run(
        [sys.executable, str(tool), *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        input=stdin,
        timeout=120,
    )


def check(label: str, condition: bool, detail: str = "") -> None:
    global CHECKS
    CHECKS += 1
    if condition:
        print(f"  ok    {label}")
    else:
        print(f"  FAIL  {label}")
        if detail:
            for line in detail.strip().splitlines()[:8]:
                print(f"          {line}")
        FAILURES.append(label)


def expect_signal(label: str, result, signal: str) -> None:
    combined = result.stdout + result.stderr
    check(label, signal in result.stdout, combined)


CONFIG = """
[project]
name = "smoke-project"

[kb]
sources = ["docs", "notes"]
extensions = [".md"]

[kb.aliases]
auth = ["authentication", "login"]

[adr]
dir = "docs/decisions"

[promote]
scratch = "tools/scratch"
dest = "tools"
registry = "docs/guides/tooling.md"
"""

NOTE = """# Session store

## Why Redis

We keep sessions in Redis rather than Postgres. See ADR 0001.

| Item | Value | Note |
|---|---|---|
| 1.10 | 512 MB | the eviction ceiling |
| 1.11 | allkeys-lru | the eviction policy |

## Authentication

Login tokens live in the same store, keyed by user id.
"""

TOOLING_DOC = """# Tooling

| Tool | What it does |
|---|---|
| `tools/existing.py` | something that already existed |
"""


def build_project(root: Path) -> None:
    (root / "docs" / "decisions").mkdir(parents=True)
    (root / "docs" / "guides").mkdir(parents=True)
    (root / "notes").mkdir()
    (root / "tools" / "scratch").mkdir(parents=True)
    (root / "claude-kit.toml").write_text(CONFIG)
    (root / "notes" / "session-store.md").write_text(NOTE)
    (root / "docs" / "guides" / "tooling.md").write_text(TOOLING_DOC)
    (root / "tools" / "scratch" / "parse_log.py").write_text("print('hello')\n")
    subprocess.run(["git", "init", "-q"], cwd=str(root), capture_output=True)


def main() -> int:
    parser = argparse.ArgumentParser(prog="smoke.py")
    parser.add_argument("--keep", action="store_true", help="leave the temp project behind")
    args = parser.parse_args()

    workdir = Path(tempfile.mkdtemp(prefix="claude-kit-smoke-"))
    root = workdir / "project"
    root.mkdir()
    print(f"smoke project: {root}\n")
    build_project(root)

    kb = bin_path("knowledge-core", "kb")
    adr = bin_path("knowledge-core", "adr")
    promote = bin_path("knowledge-core", "promote")
    brief = bin_path("session-economics", "brief")
    friction = bin_path("session-economics", "friction")
    tokencost = bin_path("session-economics", "tokencost")
    guard = PLUGINS / "session-economics" / "hooks" / "guard.py"

    # --- decision records ---------------------------------------------------
    print("adr")
    expect_signal("adr template", run(adr, ["template"], root), "ADR OK")
    expect_signal("adr new", run(adr, ["new", "Keep sessions in Redis"], root), "ADR OK")
    expect_signal("adr new (second)", run(adr, ["new", "Adopt structured logging"], root), "ADR OK")
    listing = run(adr, ["list"], root)
    expect_signal("adr list", listing, "ADR OK")
    check("adr numbered them 0001 and 0002", "0001" in listing.stdout and "0002" in listing.stdout,
          listing.stdout)
    openq = run(adr, ["open"], root)
    check("adr open shows both as proposed", openq.stdout.count("proposed") == 2, openq.stdout)
    expect_signal("adr accept", run(adr, ["accept", "1"], root), "ADR OK")
    accepted = run(adr, ["list", "--status", "accepted"], root)
    check("accept moved 0001", "0001" in accepted.stdout, accepted.stdout)
    expect_signal("adr supersede", run(adr, ["supersede", "1", "2"], root), "ADR OK")
    expect_signal("adr index", run(adr, ["index"], root), "ADR OK")
    check("index file written", (root / "docs" / "decisions" / "README.md").is_file())
    expect_signal("adr check", run(adr, ["check"], root), "ADR OK")

    # A record that violates the schema must be caught, not waved through.
    bad = root / "docs" / "decisions" / "0009-broken.md"
    bad.write_text("---\nid: 0007\nstatus: nonsense\ndate: yesterday\ntitle:\n---\n\n# broken\n")
    failing = run(adr, ["check"], root)
    check("adr check catches a bad record", "ADR CHECK FAILED" in failing.stdout, failing.stdout)
    bad.unlink()

    # --- knowledge base -----------------------------------------------------
    print("\nkb")
    expect_signal("kb build", run(kb, ["build"], root), "KB OK")
    check("index written", (root / ".kb.sqlite").is_file())
    hits = run(kb, ["search", "eviction"], root)
    expect_signal("kb search", hits, "KB OK")
    check("search found the note", "session-store" in hits.stdout, hits.stdout)

    alias = run(kb, ["search", "auth"], root)
    check("alias expanded auth to authentication", "Authentication" in alias.stdout, alias.stdout)

    rowed = run(kb, ["row", "1.10"], root)
    expect_signal("kb row", rowed, "KB OK")
    check("row carried its header", "Value" in rowed.stdout and "512 MB" in rowed.stdout, rowed.stdout)

    sect = run(kb, ["section", "session-store", "Why Redis"], root)
    expect_signal("kb section", sect, "KB OK")
    heads = run(kb, ["headings", "session-store"], root)
    expect_signal("kb headings", heads, "KB OK")
    expect_signal("kb subjects", run(kb, ["subjects"], root), "KB OK")
    expect_signal("kb stats", run(kb, ["stats"], root), "KB OK")
    expect_signal("kb pack", run(kb, ["pack", "redis", "--budget", "400"], root), "KB OK")

    why = run(kb, ["why", "redis"], root)
    expect_signal("kb why", why, "KB OK")
    check("why found the decision", "ADR 0001" in why.stdout, why.stdout)

    # The reverse edge is the point of `why`: the note cites ADR 0001, so the
    # decision must know the note leans on it.
    check("why reported the reverse edge", "cited by" in why.stdout, why.stdout)

    # A term with punctuation must not raise an FTS5 syntax error.
    punct = run(kb, ["search", "v1.3 allkeys-lru"], root)
    check("punctuated query does not error", "bad query" not in punct.stderr, punct.stderr)

    # Staleness: touch a source and the next read must rebuild rather than lie.
    (root / "notes" / "session-store.md").write_text(NOTE + "\n## Backups\n\nNightly.\n")
    stale = run(kb, ["stale"], root)
    check("stale detected the edit", "changed" in stale.stdout, stale.stdout)
    after = run(kb, ["search", "Nightly"], root)
    check("self-heal indexed the new section", "Backups" in after.stdout, after.stdout + after.stderr)

    # --- promote ------------------------------------------------------------
    print("\npromote")
    promoted = run(promote, ["parse_log.py"], root)
    expect_signal("promote", promoted, "PROMOTE OK")
    check("moved into tools/", (root / "tools" / "parse_log.py").is_file())
    check("scratch copy gone", not (root / "tools" / "scratch" / "parse_log.py").exists())
    check("shebang added", (root / "tools" / "parse_log.py").read_text().startswith("#!"))
    registry = (root / "docs" / "guides" / "tooling.md").read_text()
    check("registered a row", "tools/parse_log.py" in registry, registry)

    # --- economics ----------------------------------------------------------
    print("\neconomics")
    # No transcripts exist for a temp project, so these must degrade cleanly
    # rather than crash. That is the case a fresh machine actually hits.
    fr = run(friction, ["--brief"], root)
    check("friction runs with no log", fr.returncode == 0, fr.stdout + fr.stderr)
    tc = run(tokencost, ["--brief"], root)
    check("tokencost runs with no transcripts", tc.returncode == 0, tc.stdout + tc.stderr)
    br = run(brief, [], root)
    expect_signal("brief", br, "BRIEF OK")
    check("brief names the project", "smoke-project" in br.stdout, br.stdout)
    check("brief reports open decisions", "adr" in br.stdout, br.stdout)

    # --- hooks --------------------------------------------------------------
    print("\nhooks")
    os.environ["CLAUDE_PROJECT_DIR"] = str(root)

    big = root / "docs" / "huge.md"
    big.write_text("# Huge\n\n" + ("filler paragraph. " * 4000))
    payload = json.dumps({"tool_input": {"file_path": str(big)}})
    denied = run(guard, ["read"], root, stdin=payload)
    check("read guard denies a large unbounded read", '"deny"' in denied.stdout, denied.stdout)
    check("read guard names kb instead", "kb section" in denied.stdout, denied.stdout)

    bounded = json.dumps({"tool_input": {"file_path": str(big), "limit": 200}})
    allowed = run(guard, ["read"], root, stdin=bounded)
    check("read guard allows a bounded read", allowed.stdout.strip() == "", allowed.stdout)

    small = json.dumps({"tool_input": {"file_path": str(root / "notes" / "session-store.md")}})
    passes = run(guard, ["read"], root, stdin=small)
    check("read guard ignores a small file", passes.stdout.strip() == "", passes.stdout)

    code = json.dumps({"tool_input": {"file_path": str(root / "tools" / "parse_log.py")}})
    source_ok = run(guard, ["read"], root, stdin=code)
    check("read guard never blocks source", source_ok.stdout.strip() == "", source_ok.stdout)

    poll = json.dumps({"tool_input": {"command": "until [ -s out ]; do sleep 2; done"}})
    polled = run(guard, ["bash"], root, stdin=poll)
    check("bash guard denies a polling loop", '"deny"' in polled.stdout, polled.stdout)

    escaped = json.dumps({"tool_input": {"command": "ALLOW_POLL=1 until [ -s out ]; do sleep 2; done"}})
    escape_ok = run(guard, ["bash"], root, stdin=escaped)
    check("ALLOW_POLL escapes the poll guard", escape_ok.stdout.strip() == "", escape_ok.stdout)

    long_heredoc = "python3 - <<'PY'\n" + "x = 1\n" * 40 + "PY\n"
    hd = run(guard, ["bash"], root, stdin=json.dumps({"tool_input": {"command": long_heredoc}}))
    check("bash guard denies a long heredoc", '"deny"' in hd.stdout, hd.stdout)

    short_heredoc = "python3 - <<'PY'\nprint(1)\nPY\n"
    shd = run(guard, ["bash"], root, stdin=json.dumps({"tool_input": {"command": short_heredoc}}))
    check("bash guard allows a short heredoc", shd.stdout.strip() == "", shd.stdout)

    # The original shell version matched `python3` and `<<` anywhere in the
    # command, so a `cat <<EOF` beside a python call was a false positive.
    mixed = "cat > f.txt <<'EOF'\n" + "line\n" * 40 + "EOF\npython3 f.txt\n"
    mx = run(guard, ["bash"], root, stdin=json.dumps({"tool_input": {"command": mixed}}))
    check("cat heredoc beside python is not denied", mx.stdout.strip() == "", mx.stdout)

    for action in ["read", "bash", "depth", "friction-pre", "friction-post", "session-start"]:
        junk = run(guard, [action], root, stdin="not json at all")
        check(f"{action} fails open on garbage", junk.returncode == 0, junk.stderr)

    # The friction pair must produce a record the reader can parse back.
    call = json.dumps({"tool_use_id": "call_1", "tool_input": {"command": "ls -l"}})
    run(guard, ["friction-pre"], root, stdin=call)
    run(guard, ["friction-post"], root, stdin=call)
    log = root / "log" / "friction" / "commands.jsonl"
    check("friction hook wrote a record", log.is_file())
    if log.is_file():
        record = json.loads(log.read_text().splitlines()[0])
        check("record has the expected schema",
              set(record) == {"ts", "session", "ok", "ms", "cmd", "err"}, str(record))
        again = run(friction, ["--top", "3"], root)
        expect_signal("friction reads its own record", again, "FRICTION OK")

    # --- result -------------------------------------------------------------
    print()
    if args.keep:
        print(f"kept: {root}")
    else:
        shutil.rmtree(workdir, ignore_errors=True)

    if FAILURES:
        print(f"SMOKE FAILED: {len(FAILURES)} of {CHECKS} checks")
        for name in FAILURES:
            print(f"  - {name}")
        return 1
    print(f"{CHECKS} checks passed")
    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
