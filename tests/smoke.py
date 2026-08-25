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

    # An unknown action also returns 0, so assert the action EXISTS before
    # asserting it fails open. Otherwise this passes with an empty dispatch table.
    known = (PLUGINS / "session-economics" / "hooks" / "guard.py").read_text()
    for action in ["read", "bash", "depth", "friction-pre", "friction-post", "session-start"]:
        junk = run(guard, [action], root, stdin="not json at all")
        check(
            f"{action} fails open on garbage",
            junk.returncode == 0 and junk.stderr.strip() == "" and f'"{action}"' in known,
            junk.stderr,
        )

    # `guards.enabled = false` must silence EVERY hook, not just the refusing
    # ones. A project with its own copies needs one switch; without it the two
    # sets both fire, the brief prints twice and friction double-counts.
    config_path = root / "claude-kit.toml"
    original = config_path.read_text()
    config_path.write_text(original + "\n[guards]\nenabled = false\n")
    off_read = run(guard, ["read"], root, stdin=payload)
    check("enabled=false silences the read guard", off_read.stdout.strip() == "", off_read.stdout)
    off_bash = run(guard, ["bash"], root, stdin=poll)
    check("enabled=false silences the bash guard", off_bash.stdout.strip() == "", off_bash.stdout)
    off_brief = run(guard, ["session-start"], root, stdin=json.dumps({"source": "startup"}))
    check("enabled=false silences the brief", off_brief.stdout.strip() == "", off_brief.stdout)
    marker = json.dumps({"tool_use_id": "off_1", "tool_input": {"command": "ls"}})
    run(guard, ["friction-pre"], root, stdin=marker)
    run(guard, ["friction-post"], root, stdin=marker)
    log_path = root / "log" / "friction" / "commands.jsonl"
    before = log_path.read_text() if log_path.is_file() else ""
    check("enabled=false records no friction", "off_1" not in before and "ls" not in before, before)
    config_path.write_text(original)

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

    # --- regressions the review found ---------------------------------------
    # Each of these shipped broken once. The check is cheaper than the bug.
    print("\nregressions")

    # A batch FILE needs `%%I`; the `%I` command-line form is a hard syntax
    # error, and two launchers had it, so `prose` and `brief` died on Windows.
    bad_cmd = []
    for cmd in sorted(PLUGINS.glob("*/bin/*.cmd")):
        body = cmd.read_text(encoding="utf-8", errors="replace")
        if "for %%I in" not in body:
            bad_cmd.append(cmd.name)
    check("every .cmd uses the batch-file %%I form", not bad_cmd, ", ".join(bad_cmd))

    # `adr index` wrote its README unconditionally, destroying a hand-written one.
    handwritten = root / "docs" / "decisions" / "README.md"
    handwritten.write_text("# My own index\n\nThis took an hour.\n")
    run(adr, ["index"], root)
    backups = list((root / "docs" / "decisions").glob("README.md.bak-*"))
    check("adr index preserved a foreign README", bool(backups), str(backups))
    if backups:
        check("the backup holds the original text", "took an hour" in backups[0].read_text())
        for stale in backups:
            stale.unlink()

    # The friction log records every command run, so the directory has to
    # exclude itself the moment it appears, not when somebody remembers.
    ignore = root / "log" / ".gitignore"
    check("log/ ignores itself", ignore.is_file() and "*" in ignore.read_text(), str(ignore))

    # `promote` read with errors="replace" and wrote the result back, which
    # turned any undecodable byte into U+FFFD after the move had already
    # happened, leaving nothing to recover from.
    binary = root / "tools" / "scratch" / "latin.py"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b"# caf\xe9 was here\nprint(1)\n")
    run(promote, ["latin.py"], root)
    moved = root / "tools" / "latin.py"
    check("promote preserved a non-UTF-8 byte", moved.is_file() and b"\xe9" in moved.read_bytes(),
          repr(moved.read_bytes()[:40]) if moved.is_file() else "not moved")

    # `promote` printed "the row is there" whether or not it had written one.
    (root / "tools" / "scratch" / "orphan.py").write_text("print(2)\n")
    (root / "docs" / "guides" / "tooling.md").write_text("# Tooling\n\nNo table here.\n")
    orphan = run(promote, ["orphan.py"], root)
    check("promote admits it wrote no row", "no table row there matched" in orphan.stdout,
          orphan.stdout)

    # Bare `adr` crashed: --status lives only on the `list` subparser.
    bare = run(adr, [], root)
    check("bare adr does not crash", bare.returncode == 0 and "Traceback" not in bare.stderr,
          bare.stderr)

    # fm_write walked past an unclosed fence and rewrote matching body lines.
    broken = root / "docs" / "decisions" / "0009-unclosed.md"
    broken.write_text("---\nid: 0009\nstatus: proposed\n\n# no fence\n\nstatus: prose line\n")
    run(adr, ["accept", "9"], root)
    check("unclosed front matter is refused, not mangled",
          "status: prose line" in broken.read_text(), broken.read_text())
    broken.unlink()

    # A one-line status change must not rewrite the body.
    fenced = root / "docs" / "decisions" / "0010-fenced.md"
    fenced.write_text(
        "---\nid: 0010\nstatus: proposed\ndate: 2026-01-01\ntitle: T\n---\n\nstatus: prose\n"
    )
    run(adr, ["accept", "10"], root)
    body = fenced.read_text()
    check("status change left the body alone",
          "status: accepted" in body and "status: prose" in body, body)
    fenced.unlink()

    # The poll guard denied ordinary commands and missed real loops.
    must_pass = [
        "git commit -m 'retry loop for the flaky sleep test'",
        "for f in logs/*; do grep -c sleep \"$f\"; done",
        "for f in *.c; do gcc -c $f; done && sleep 1",
        "rg -n 'while|sleep' src/",
        "awk '{for(i=1;i<=NF;i++)s+=$i}END{print s}' /var/log/sleep.log",
    ]
    for command in must_pass:
        got = run(guard, ["bash"], root, stdin=json.dumps({"tool_input": {"command": command}}))
        check(f"allowed: {command[:44]}", got.stdout.strip() == "", got.stdout)

    must_deny = [
        "until [ -f done ]; do usleep 200000; done",
        "watch -n 5 'ls out'",
        'while ! grep -q DONE /tmp/log; do echo "' + "x" * 420 + '"; sleep 5; done',
        'echo "prefix with ALLOW_POLL=1"; until [ -s o ]; do sleep 1; done',
    ]
    for command in must_deny:
        got = run(guard, ["bash"], root, stdin=json.dumps({"tool_input": {"command": command}}))
        check(f"denied: {command[:44]}", '"deny"' in got.stdout, got.stdout)

    escape = run(guard, ["bash"], root, stdin=json.dumps(
        {"tool_input": {"command": "ALLOW_POLL=1 until [ -s o ]; do sleep 1; done"}}))
    check("a real ALLOW_POLL=1 assignment escapes", escape.stdout.strip() == "", escape.stdout)

    # The heredoc guard fired on a left-shift and on a cat heredoc beside python.
    shift = "python3 tool.py --shift '1<<20'\n" + "# pad\n" * 40
    got = run(guard, ["bash"], root, stdin=json.dumps({"tool_input": {"command": shift}}))
    check("a left-shift is not a heredoc", got.stdout.strip() == "", got.stdout)

    mixed_one_line = "python3 -c 'print(1)' && cat > f.txt <<'EOF'\n" + "x\n" * 40 + "EOF\n"
    got = run(guard, ["bash"], root, stdin=json.dumps({"tool_input": {"command": mixed_one_line}}))
    check("cat heredoc after python on one line is allowed", got.stdout.strip() == "", got.stdout)

    real = "sqlite3 db <<SQL\n" + "select 1;\n" * 40 + "SQL\n"
    got = run(guard, ["bash"], root, stdin=json.dumps({"tool_input": {"command": real}}))
    check("a long sqlite3 heredoc is denied", '"deny"' in got.stdout, got.stdout)

    # The read guard named a subject kb does not know, and ignored skip_dirs.
    generic_dir = root / "docs" / "architecture"
    generic_dir.mkdir(parents=True, exist_ok=True)
    generic_doc = generic_dir / "README.md"
    generic_doc.write_text("# Arch\n\n" + ("filler paragraph. " * 4000))
    got = run(guard, ["read"], root,
              stdin=json.dumps({"tool_input": {"file_path": str(generic_doc)}}))
    check("read guard names the real subject", "architecture-readme" in got.stdout, got.stdout)

    skipped_dir = root / "docs" / "_out"
    skipped_dir.mkdir(parents=True, exist_ok=True)
    skipped_doc = skipped_dir / "report.md"
    skipped_doc.write_text("# Report\n\n" + ("filler paragraph. " * 4000))
    got = run(guard, ["read"], root,
              stdin=json.dumps({"tool_input": {"file_path": str(skipped_doc)}}))
    check("read guard ignores a skipped directory", got.stdout.strip() == "", got.stdout)

    for value in (False, 0):
        got = run(guard, ["read"], root, stdin=json.dumps(
            {"tool_input": {"file_path": str(big), "offset": value}}))
        expected = value is not False  # an int bounds the read; a bool does not
        check(f"offset={value!r} bounds the read: {expected}",
              (got.stdout.strip() == "") is expected, got.stdout)

    # The index regressions need a project that has NEVER been built, because
    # the bug was that asking about the index created a broken one. Doing this
    # in the main project would be hidden by the `kb build` at the top.
    print("\nindex regressions")
    fresh = workdir / "fresh"
    (fresh / "docs" / "decisions").mkdir(parents=True)
    (fresh / "claude-kit.toml").write_text(
        '[kb]\nsources = ["docs", "docs"]\nextensions = [".md"]\n\n'
        '[kb.aliases]\ndma = "direct memory access"\n\n'
        '[adr]\ndir = "docs/decisions"\n'
    )
    (fresh / "docs" / "note.md").write_text(
        "# Note\n\n## Zebra\n\nZebras are striped, ratio 2:1.\n"
    )
    subprocess.run(["git", "init", "-q"], cwd=str(fresh), capture_output=True)

    # `kb stale` connected before checking, and sqlite3.connect CREATES the file.
    # `brief` runs `kb stale` at session start, so the index was permanently
    # broken before anyone ever searched.
    run(brief, [], fresh)
    hits = run(kb, ["search", "zebra"], fresh)
    check("search works after brief ran first", "KB OK" in hits.stdout, hits.stdout + hits.stderr)
    check("no empty index left behind",
          (fresh / ".kb.sqlite").stat().st_size > 0 if (fresh / ".kb.sqlite").exists() else False)

    # A duplicated source made walk() yield twice; build dropped the duplicate
    # and stale counted it, so every query rebuilt the whole index.
    run(kb, ["build"], fresh)
    drift = run(kb, ["stale"], fresh)
    check("no phantom drift after a build", "index is current" in drift.stdout, drift.stdout)

    # A punctuated question must return RESULTS, not merely avoid an error.
    punct = run(kb, ["search", "ratio 2:1"], fresh)
    check("a colon query still finds the section", "Zebra" in punct.stdout,
          punct.stdout + punct.stderr)
    for query in ["C++ (draft)", "*args handling", "a AND", "-"]:
        got = run(kb, ["search", query], fresh)
        check(f"no syntax error for {query!r}", "bad query" not in got.stderr, got.stderr)

    # An alias written as a string was iterated character by character.
    alias = run(kb, ["search", "dma"], fresh)
    check("a string alias is one phrase, not 20 letters",
          "bad query" not in alias.stderr and alias.returncode == 0, alias.stderr)

    # pack with a nonsense budget sliced from the end and called it truncated.
    for value in ["0", "-10"]:
        got = run(kb, ["pack", "zebra", "--budget", value], fresh)
        check(f"pack survives --budget {value}", "KB OK" in got.stdout, got.stdout + got.stderr)

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
