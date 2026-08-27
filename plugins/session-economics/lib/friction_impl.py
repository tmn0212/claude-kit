#!/usr/bin/env python3
"""Report where agent sessions actually spend wall-clock time, and what they gave up on.

Reads log/friction/commands.jsonl, written by this plugin's own PreToolUse and
PostToolUse hooks. Those hooks capture; this derives. Nothing here asserts that a
command is slow: it reports what was measured.

    friction                 full report
    friction --brief         the few lines the session brief embeds
    friction --top 20        deeper slow list
    friction --since 7       last 7 days only
    friction --session LAST  most recent session only
    friction --prune         drop records past the retention window

FRICTION OK on stdout is the success signal. Grep for the line, not exit code 0.

Two signals matter more than raw totals:

  Unresolved failures  A command that failed in a session and never afterwards
                       succeeded in that same session was worked around, not fixed.
                       That is the trap the next session walks into.

  Repeat count         The same command run many times is usually a missing cache,
                       a missing flag, or an answer that should have been written
                       down somewhere durable.
"""

import argparse
import json
import re
import sys
import time
from collections import defaultdict
from pathlib import Path

from kit_config import Config

_CFG = Config.load()

LOG = _CFG.root / "log" / "friction" / "commands.jsonl"

# A bare relative path is likely rooted at one of these, so a failure naming
# one is probably a directory change that broke every path after it.
_ROOT_RELATIVE = "^(" + "|".join(
    re.escape(d) for d in _CFG.get("economics.root_relative_dirs", ["tools", "docs"])
) + ")/"

# Programs that do nothing on their own: the subcommand is the real identity, so
# `git status` and `git push` are counted separately rather than both as "git".
MULTIPLEXERS = {
    "git", "npm", "npx", "pnpm", "yarn", "cargo", "docker", "apt", "apt-get",
    "pip", "pip3", "python", "python3", "go", "systemctl", "gh", "cmake",
    "make", "claude",
    # Interpreters and launchers. Without these, every `py x.py`, every `bash y.sh`
    # and every `xcrun devicectl ...` collapses into a single bucket named after the
    # interpreter, which is useless for both the report and anything reading a
    # duration off it: `py` was measuring 32 s here because one slow script was
    # averaged in with every quick one. `py` in particular is the Windows launcher,
    # and is what this kit's own python option is usually set to.
    "py", "node", "bash", "sh", "zsh", "perl", "ruby", "gradlew", "xcrun", "adb",
} | set(_CFG.get("economics.multiplexers", []))

# Shell prologue and pure-output builtins. An agent's Bash call routinely opens with
# `set -u`, a `cd`, and a few `echo` labels; bucketing on those would make the whole
# report say "you spend all your time running `echo`".
TRIVIAL = {
    "set", "cd", "export", "echo", "printf", "true", "false", ":", "source", ".",
    "shopt", "umask", "unset", "alias", "pwd", "read",
    # Control flow, never a program worth bucketing on. `exit` matters most: the
    # `cd "$REPO" || exit 1` prologue is one of the commonest ways an agent opens a
    # command, and because `cd` is skipped here the `|| exit 1` segment used to win
    # and return before the real command on the next line was ever looked at. On one
    # project that made `exit` the single largest reported time sink, 1h05m over 160
    # calls, and it was not a sink at all: it was grep, rrun, find and the rest
    # wearing a mislabel, with `brief` reprinting it at the top of every session.
    "exit", "return", "break", "continue",
    "[", "[[", "local", "declare", "typeset", "trap", "shift", "wait",
    # NOT `test`. It is a shell builtin, but it is also a very ordinary name for a
    # repo's own entry point, and `_program` reduces `./test` and `bin/test` to the
    # basename `test`. Listing it here would make `./test && ./deploy.sh` bucket as
    # `./deploy.sh`, which is this exact bug wearing a different name. `npm test`
    # and `go test` are unaffected either way: there `test` is a subcommand reached
    # through MULTIPLEXERS, never the leading token.
}

_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S*$")
_SPLIT = re.compile(r"(?:\|\||&&|\||;|\n)")


def _program(segment):
    """(program, subcommand-qualified name) for one shell segment, or None."""
    parts = segment.strip().split()
    while parts and _ENV_ASSIGN.match(parts[0]):
        parts.pop(0)
    if not parts:
        return None
    prog = parts[0]
    base = prog.rsplit("/", 1)[-1]
    if base in MULTIPLEXERS:
        # Prefer a bare word (`push`, `flash`, `build`) over anything that looks like
        # a flag's value, so `git -C /some/path status` is `git status` rather
        # than `git /some/path`. Falling back to the first non-flag token keeps
        # `python3 tools/foo.py` identifiable instead of collapsing to `python3`.
        candidates = [t for t in parts[1:] if not t.startswith("-")]
        bare = [
            t for t in candidates
            if "/" not in t and "=" not in t and not t[:1].isdigit()
        ]
        pick = bare[0] if bare else (candidates[0] if candidates else None)
        if pick:
            return base, f"{prog} {pick}"
    return base, prog


def shape(cmd):
    """Reduce a command line to the identity worth aggregating on.

    Takes the first *substantive* segment of a compound command, skipping shell
    prologue, dropping env assignments and flags, and keeping the subcommand for
    multiplexers. `set -u; echo hi; FOO=1 git -c x status --short | wc` becomes
    `git status`.
    """
    fallback = None
    for segment in _SPLIT.split(cmd.strip()):
        segment = segment.strip()
        if not segment or segment.startswith("#"):
            continue
        got = _program(segment)
        if got is None:
            continue
        base, name = got
        if fallback is None:
            fallback = name
        if base not in TRIVIAL:
            return name
    return fallback or (cmd.strip()[:40] or "?")


def load(since_days=None):
    if not LOG.exists():
        return []
    cutoff = None
    if since_days:
        cutoff = time.time() - since_days * 86400
    out = []
    for line in LOG.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue  # a torn line from a killed hook; skip it rather than die
        if cutoff:
            try:
                ts = time.mktime(time.strptime(rec["ts"], "%Y-%m-%dT%H:%M:%SZ"))
            except (KeyError, ValueError):
                ts = None
            if ts is not None and ts < cutoff:
                continue
        rec["shape"] = shape(rec.get("cmd", ""))
        out.append(rec)
    return out


def flatten(text):
    """One line. Tool errors are multi-line ("Exit code 2\\nls: cannot access...") and
    would otherwise break the single-line format the brief depends on."""
    return " / ".join(part.strip() for part in text.splitlines() if part.strip())


def human(ms):
    if ms is None:
        return "-"
    s = ms / 1000.0
    if s < 1:
        return f"{ms}ms"
    if s < 60:
        return f"{s:.1f}s"
    m, s = divmod(int(s), 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def aggregate(records):
    agg = defaultdict(lambda: {"n": 0, "ms": 0, "timed": 0, "fail": 0})
    for r in records:
        a = agg[r["shape"]]
        a["n"] += 1
        if not r.get("ok", True):
            a["fail"] += 1
        if isinstance(r.get("ms"), int):
            a["ms"] += r["ms"]
            a["timed"] += 1
    return agg


def blame(err, fallback):
    """Name the segment that actually failed, not the one `shape()` aggregates on.

    `shape()` deliberately skips trivial programs like `cd` and reports the first
    substantive segment, which is right for timing. It is wrong for failures: in
    `cd sub/dir && python3 - <<PY ... && ./scripts/x.sh`, the python succeeds and the
    LAST segment fails, yet the report blamed python. That misattribution sent a
    session chasing a heredoc bug that did not exist. The error text names the real
    culprit, so use it.
    """
    if not err:
        return fallback, None
    text = flatten(err)
    m = re.search(r"(?:line \d+: )?([^\s:]+): (?:command not found|No such file or directory)", text)
    if not m:
        m = re.search(r"([^\s:]+): cannot access", text)
    if not m:
        return fallback, None
    culprit = m.group(1).rsplit("/", 1)[-1] if m.group(1).startswith("/") else m.group(1)
    note = None
    # A repo-root-relative path that stopped resolving is almost always a `cd`
    # earlier in the same compound command, not a missing file.
    if culprit.startswith("./") or re.match(_ROOT_RELATIVE, culprit):
        note = "repo-root-relative path used after a cd in the same command"
    elif "cd:" in text or re.search(r"line \d+: cd:", text):
        note = "the cd itself failed; later segments then ran in the wrong directory"
    return (culprit if culprit != fallback else fallback), note


def unresolved(records):
    """Command shapes that failed in a session and never succeeded in it afterwards.

    Ordering matters: a failure followed by a success is a fix, and a success
    followed by a failure is a regression that was left behind. Only the latter,
    and never-succeeded shapes, count as worked around.
    """
    last_ok = defaultdict(lambda: None)
    last_fail = defaultdict(lambda: None)
    errors = {}
    for i, r in enumerate(records):
        key = (r.get("session", "?"), r["shape"])
        if r.get("ok", True):
            last_ok[key] = i
        else:
            last_fail[key] = i
            if r.get("err"):
                errors[key] = r["err"]

    out = []
    for key, fail_i in last_fail.items():
        ok_i = last_ok[key]
        if ok_i is None or ok_i < fail_i:
            fails = sum(
                1 for r in records
                if (r.get("session", "?"), r["shape"]) == key and not r.get("ok", True)
            )
            err = errors.get(key, "")
            culprit, note = blame(err, key[1])
            out.append((key[0], key[1], fails, err, culprit, note))
    return sorted(out, key=lambda t: -t[2])


def prune(keep_days, max_records):
    """Bound the capture log. Returns (dropped, kept).

    The log is append-only and nothing else trims it: tools/logclean.sh archives
    whole files per category, which does not fit one growing JSONL. Retention is
    primarily by age, because the reports are time-windowed (`--since`), with a
    record cap as a backstop against a pathological burst.

    Rewrites only when something would actually be dropped. A hook in a concurrent
    session could append between the read and the rename and lose that one record;
    keeping the rewrite rare keeps that window rare, and one lost timing sample is
    not worth a lock.
    """
    if not LOG.exists():
        return 0, 0

    lines = [ln for ln in LOG.read_text(encoding="utf-8", errors="replace").splitlines() if ln.strip()]
    total = len(lines)
    cutoff = time.time() - keep_days * 86400

    kept = []
    for ln in lines:
        try:
            ts = time.mktime(time.strptime(json.loads(ln)["ts"], "%Y-%m-%dT%H:%M:%SZ"))
        except (json.JSONDecodeError, KeyError, ValueError):
            continue  # unparseable or torn: dropping it is the point of a prune
        if ts >= cutoff:
            kept.append(ln)

    if len(kept) > max_records:
        kept = kept[-max_records:]

    dropped = total - len(kept)
    if dropped <= 0:
        return 0, total

    tmp = LOG.with_suffix(".jsonl.tmp")
    tmp.write_text("\n".join(kept) + ("\n" if kept else ""))
    tmp.replace(LOG)
    return dropped, len(kept)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--brief", action="store_true", help="compact form for tools/brief.sh")
    ap.add_argument("--top", type=int, default=8, help="rows in each ranking (default 8)")
    ap.add_argument("--since", type=float, metavar="DAYS", help="only records this recent")
    ap.add_argument("--session", metavar="ID", help="one session id, or LAST")
    ap.add_argument("--prune", action="store_true",
                    help="drop records past the retention window, then exit")
    ap.add_argument("--keep-days", type=float, default=60, metavar="N",
                    help="retention window for --prune (default 60)")
    ap.add_argument("--max-records", type=int, default=20000, metavar="N",
                    help="hard cap for --prune, applied after age (default 20000)")
    args = ap.parse_args()

    if args.prune:
        dropped, kept = prune(args.keep_days, args.max_records)
        if dropped:
            print(f"friction: pruned {dropped} record(s), {kept} kept")
        print("FRICTION OK")
        return 0

    records = load(args.since)

    if args.session:
        if not records:
            pass
        elif args.session.upper() == "LAST":
            wanted = records[-1].get("session")
            records = [r for r in records if r.get("session") == wanted]
        else:
            records = [r for r in records if r.get("session", "").startswith(args.session)]

    if not records:
        if not args.brief:
            print("No friction data yet.")
            print(f"  Expected: {LOG}")
            print("  This plugin's Bash hooks populate it, one line per command, once you")
            print("  run something. If it stays empty, check session-economics is enabled:")
            print("    claude plugin list")
            print("FRICTION OK")
        return 0

    agg = aggregate(records)
    stuck = unresolved(records)
    sessions = len({r.get("session") for r in records})
    total_ms = sum(a["ms"] for a in agg.values())

    if args.brief:
        by_time = sorted(agg.items(), key=lambda kv: -kv[1]["ms"])[:2]
        bits = [f"{k} {human(v['ms'])}/{v['n']}x" for k, v in by_time if v["ms"]]
        if bits:
            print(f"  time sinks: {', '.join(bits)}")
        for session, sh, n, err, _culprit, _note in stuck[:3]:
            detail = f" ({flatten(err)[:60]})" if err else ""
            print(f"  UNFIXED: {sh} failed {n}x in session {session[:8]}, never succeeded{detail}")
        # The instruction lives here rather than in CLAUDE.md on purpose: it appears
        # at the moment it applies, attached to the specific thing that broke, where
        # a standing rule competing with fifty others would not.
        if stuck:
            print("  ^ fix the cause before building on it; a second workaround makes it permanent.")
        return 0

    print(f"FRICTION  {len(records)} calls, {sessions} session(s), {human(total_ms)} measured")
    print()

    print("Slowest by total time")
    by_time = sorted(agg.items(), key=lambda kv: -kv[1]["ms"])[: args.top]
    any_time = False
    for sh, a in by_time:
        if not a["ms"]:
            continue
        any_time = True
        avg = a["ms"] // max(a["timed"], 1)
        print(f"  {human(a['ms']):>8}  {a['n']:>4}x  {sh:<42} avg {human(avg)}")
    if not any_time:
        print("  (no timed calls yet)")

    print()
    print("Most repeated")
    for sh, a in sorted(agg.items(), key=lambda kv: -kv[1]["n"])[: args.top]:
        flag = f"  [{a['fail']} failed]" if a["fail"] else ""
        print(f"  {a['n']:>4}x  {sh}{flag}")

    print()
    if stuck:
        print("Failed and never resolved  <- these are the traps the next session inherits")
        for session, sh, n, err, culprit, note in stuck[: args.top]:
            label = sh if culprit == sh else f"{sh}  -> blame: {culprit}"
            print(f"  {label:<42} {n}x  session {session[:8]}")
            if err:
                print(f"      last error: {flatten(err)[:100]}")
            if note:
                print(f"      likely cause: {note}")
        print()
        print("  Fix the cause, or encode the workaround as a check in the tool that hits it.")
        print("  A note in a doc is advisory; a tool that warns at the point of use is not.")
    else:
        print("Failed and never resolved: none")

    print()
    print("FRICTION OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
