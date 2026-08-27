#!/usr/bin/env python3
"""Every hook in the session-economics plugin, behind one subcommand.

One script rather than six, because each hook is thirty lines of policy around
the same payload read, and six copies of that would drift.

    guard.py read           PreToolUse(Read)     refuse an unbounded whole-file read
    guard.py bash           PreToolUse(Bash)     refuse polling loops and long heredocs
    guard.py depth          UserPromptSubmit     report context depth on a crossing
    guard.py friction-pre   PreToolUse(Bash)     stamp a start time
    guard.py friction-post  PostToolUse(Bash)    record the outcome
    guard.py friction-fail  PostToolUseFailure   record a failed command
    guard.py session-start  SessionStart         print the brief

HARD RULES, because these run before every tool call in a project:

1. **Fail open, always.** Any error, any unparsable payload, any missing file:
   exit 0 with empty stdout, which defers to the normal permission flow. A
   broken hook here must never be able to block work.
2. **Deny only the narrow shapes, and always name what to run instead.** A
   refusal that does not say what would have worked is just an obstacle.
3. **Stay cheap.** Stdlib only, no subprocess except where the job is to run
   something. These files are read on every call.

Why hooks rather than a line in the project's instructions: an instruction is a
message at the top of the context window, and by the time a session is 400k
tokens deep it is 400k tokens away from the decision it is meant to govern. A
hook runs regardless of depth and cannot be forgotten.
"""

from __future__ import annotations

import datetime
import json
import os
import re
import shlex
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

try:
    from kit_config import Config
except Exception:  # pragma: no cover - fail open on any import trouble
    Config = None


# --- plumbing -------------------------------------------------------------


def payload() -> dict:
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    if not raw.strip():
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {}


def config():
    if Config is None:
        return None
    try:
        return Config.load(os.environ.get("CLAUDE_PROJECT_DIR"))
    except Exception:
        return None


def setting(cfg, dotted, default):
    if cfg is None:
        return default
    try:
        value = cfg.get(dotted, default)
        return default if value is None else value
    except Exception:
        return default


_EMITTED = False


def emit(obj) -> None:
    """Write the hook's one reply.

    FIRST WRITER WINS, and that is load-bearing rather than tidy: the harness
    reads a single JSON object from stdout, so a second one would corrupt the
    reply and the whole hook would be discarded. A refusal is always decided
    before an advisory is offered, so keeping the first is also the right
    precedence: being told no outranks being told a tip.
    """
    global _EMITTED
    if _EMITTED:
        return
    try:
        sys.stdout.write(json.dumps(obj))
        _EMITTED = True
    except Exception:
        pass


def deny(event: str, reason: str) -> None:
    emit(
        {
            "hookSpecificOutput": {
                "hookEventName": event,
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
    )


def advise(event: str, summary: str, context: str) -> None:
    """Say something without refusing anything.

    Some costs are real but are not mistakes, so a denial would be wrong: the
    call is legitimate, it is the SHAPE OF THE SEQUENCE around it that is
    expensive. Those get a note instead, and each one is written to fire once
    per run of the behaviour rather than on every call, because an advisory
    that repeats is one that gets tuned out.
    """
    emit(
        {
            "systemMessage": summary,
            "hookSpecificOutput": {
                "hookEventName": event,
                "additionalContext": context,
            },
        }
    )


def enabled(cfg) -> bool:
    """Whether this plugin's hooks should do anything at all.

    `guards.enabled = false` turns off EVERY hook the plugin installs, not just
    the refusing ones. That matters for a project that already has its own
    copies: without a single switch, the two sets both fire, the brief prints
    twice and every Bash call is recorded twice, which quietly doubles the
    numbers `friction` reports.
    """
    return bool(setting(cfg, "guards.enabled", True))


# --- 1. read guard --------------------------------------------------------


def hook_read() -> None:
    """Refuse an unbounded whole-file Read of a large indexed document.

    A tool result is re-read by every request that follows it, so its real cost
    is size times the requests after it. That makes a single large Read the most
    expensive thing a session can do, and the one the harness does not already
    truncate.

    Scope is deliberately narrow: only documents inside the trees `kb` indexes,
    so there is always a better call to name. Source files, images, logs and
    anything outside those trees pass through untouched. This must never stand
    between a session and code it has to read.

    It refuses rather than silently truncating. Silent truncation hands back a
    partial file that reads as a whole one, which is how a session concludes a
    fact is absent when it is merely off the end.
    """
    data = payload()
    cfg = config()
    if not enabled(cfg):
        return
    path = (data.get("tool_input") or {}).get("file_path")
    if not path:
        return

    # An explicit offset or limit is a deliberate, bounded read. Always allow
    # it. An INTEGER, specifically: `is not None` let `false` through while
    # rejecting `null`, so two spellings of the same nonsense disagreed.
    tool_input = data.get("tool_input") or {}
    if any(
        isinstance(tool_input.get(key), int) and not isinstance(tool_input.get(key), bool)
        for key in ("offset", "limit")
    ):
        return

    if cfg is None:
        return
    sources = [str(s).strip("/") for s in setting(cfg, "kb.sources", [])]
    extensions = tuple(setting(cfg, "kb.extensions", [".md"]))
    if not str(path).endswith(extensions):
        return
    try:
        relative = Path(path).resolve().relative_to(cfg.root).as_posix()
    except Exception:
        return
    if not any(relative.startswith(s + "/") for s in sources):
        return
    # A document inside a skipped directory was never indexed, so refusing the
    # Read would name a `kb` command that cannot work. Let it through.
    skip = set(setting(cfg, "kb.skip_dirs", []))
    if any(part in skip for part in Path(relative).parts[:-1]):
        return

    threshold = int(setting(cfg, "guards.read_bytes", 24000))
    try:
        size = Path(path).stat().st_size
    except OSError:
        return
    if size <= threshold:
        return

    # The same rule kb uses: a generic stem takes its directory name, so a
    # 50 KB docs/architecture/README.md is subject `architecture-readme`, not
    # `README`. Naming the wrong subject makes both suggested commands miss.
    generic = {str(s).lower() for s in setting(cfg, "kb.generic_stems", [])}
    stem = Path(path).stem
    subject = f"{Path(path).parent.name}-{stem.lower()}" if stem.lower() in generic else stem
    deny(
        "PreToolUse",
        f"{relative} is ~{size // 4} tokens, and a Read result is re-billed on "
        "every request that follows it.\n\n"
        "Prefer one of these:\n"
        f"  kb headings {subject}            the table of contents\n"
        f"  kb section {subject} '<heading>'  just the section you need\n"
        "  kb search '<terms>'               if you do not know the heading yet\n\n"
        "If you do need this file directly, re-issue the Read with an explicit "
        "offset or limit (for the whole file, limit: 2000). That is always allowed.",
    )


# --- 2. bash guard --------------------------------------------------------

# A polling loop is a loop whose BODY sleeps. Both halves are load-bearing.
# An earlier version matched any loop keyword within 400 characters of the word
# "sleep", which denied `git commit -m 'retry loop for the flaky sleep test'`
# while missing `do usleep 1; done`.
#
# Non-greedy on BOTH spans. Greedy `.*` between `do` and `done` swallows a whole
# command, so `grep -n 'do sleep 2; done' FILE` reads as a loop body, and so does
# any pair of unrelated loops with a sleep between them.
# The body span is BOUNDED. Unbounded `(.*?)` backtracks: 800 unclosed `do`
# in 36 KB took 6.3 seconds, on a pattern that runs before every Bash call.
# A loop body worth refusing is never four thousand characters long.
_LOOP_BODY = re.compile(
    r"\b(?:until|while|for)\b[^\n]{0,300}?\bdo\b(.{0,4000}?)\bdone\b", re.S
)

# The start of a command: line start, or after a separator. Everything below
# anchors here, because matching a NAME anywhere is what produced every false
# positive: `python3` inside `cat > python3.py`, `node` inside a path.
_CMD_START = r"(?:^|[;&|(]|&&|\|\||\bdo\b|\bthen\b)\s*"
# An optional path in front of the program, so `/bin/sleep` and
# `./.venv/bin/python3` are recognised. The last one is this project's own
# convention, and it escaped the guard entirely.
_PATHED = r"(?:[\w.@/-]*/)?"

_SLEEP_CALL = re.compile(_CMD_START + _PATHED + r"u?sleep\b", re.M)
# `watch` is polling with the loop moved into another program. `timeout N watch`
# and a `watch` on its own line both count.
_WATCH = re.compile(_CMD_START + r"(?:timeout\s+\S+\s+)?" + _PATHED + r"watch\b", re.M)
# The escape must be an assignment at a command position, not the word inside a
# string. It disarms the whole command, which is the documented behaviour.
_ALLOW_POLL = re.compile(_CMD_START + r"ALLOW_POLL=1\b", re.M)
# A program whose whole job is to run something many times. With a sleep at a
# command position that IS a polling loop, with or without `do`/`done`.
_REPEATER = re.compile(r"(?:^|[;&|]|&&)\s*(?:xargs|parallel)\b")
# `sh -c` hides an entire loop inside one quoted argument.
_SHELL_C = re.compile(r"\b(?:ba|z|k|a)?sh\b[^\n]*?\s-c(?:\s|$)")
# An interpreter heredoc.
#
# Three anchors, each closing a real hole. The interpreter must be at a COMMAND
# position with an optional path, so `cat > python3.py <<EOF` is not a match and
# `/usr/bin/python3 <<PY` is. `[^\n;&|]*` keeps the interpreter and the `<<` in
# one segment, so `python3 -c '...' && cat > f <<EOF` is not a match. And the
# opener may be followed by a redirect or a pipe, because `python3 - <<PY > out`
# is still a heredoc and the old `$` anchor let it through.
#
# The delimiter is captured and must also appear alone on a later line, which is
# what separates a real heredoc from a left shift like `--shift '1<<20'`.
_INTERPRETERS = r"(?:python3?|node|perl|ruby|bun|deno|ipython|sqlite3)"
_HEREDOC = re.compile(
    _CMD_START + _PATHED + _INTERPRETERS + r"\b"
    r"[^\n;&|]*<<-?\s*[\'\"]?(\w+)[\'\"]?\s*(?:[<>|&].*)?$",
    re.M,
)


# A quoted span. Removing these before the top-level scan is what separates
# searching FOR the banned shape from running it.
_QUOTED = re.compile(r"'[^']*'|\"[^\"]*\"")


def strip_quotes(command: str) -> str:
    """The command with quoted spans blanked out, newlines preserved.

    Blanked rather than deleted, so a heredoc body stays the right number of
    lines and the line count the heredoc rule reports is still true.
    """
    return _QUOTED.sub(lambda m: re.sub(r"[^\n]", " ", m.group(0)), command)


def shell_fragments(command: str):
    """The command, plus every string it hands to `sh -c`.

    A polling loop hides easily: `xargs -I{} sh -c 'sleep 2; test -f out'` has
    no `do`/`done` anywhere at the top level. Widening the outer regex to reach
    inside the quotes is what produced false positives on ordinary commands, so
    this parses the quoting instead of pattern-matching it, and the unchanged
    rules then apply to what comes out.
    """
    yield command
    if not _SHELL_C.search(command):
        return
    try:
        tokens = shlex.split(command)
    except ValueError:
        # Unbalanced quotes. Not parseable, so there is nothing to look inside.
        return
    for index, token in enumerate(tokens):
        if token == "-c" and index + 1 < len(tokens):
            yield tokens[index + 1]


_REMOTE_DEFAULT = [
    r"(?:^|[|;&(\s])ssh\s+[^-\s]",      # `ssh host ...`, not `ssh-keygen`
    r"(?:^|[|;&(\s])rrun\b",
    r"(?:^|[|;&(\s])scp\s",
    r"\bdocker\s+exec\b",
    r"\bkubectl\s+exec\b",
]


def remote_burst(cfg, data, command) -> bool:
    """True once, when a run of back-to-back remote calls gets long enough to matter.

    Every remote call pays a fixed toll before it does any work: a process
    launch, a connection, an authentication. Paying it once for fifteen
    questions is one toll; paying it fifteen times is fifteen, and the work in
    between is usually nothing at all. Measured on one project: five hours
    across 63 sessions sat in runs of three or more consecutive remote calls,
    the longest being fifteen in a row.

    Deliberately an advisory and not a refusal. Any single one of those calls is
    a perfectly reasonable thing to run; only the sequence is wasteful, and a
    hook cannot know whether the next command depends on this one's output.

    Fires ONCE per run: the counter is reset by the advisory itself, so a burst
    of thirty produces one note rather than twenty-six.
    """
    patterns = cfg.get("economics.remote_patterns", _REMOTE_DEFAULT) if cfg else _REMOTE_DEFAULT
    threshold = int(cfg.get("economics.remote_burst", 4) if cfg else 4)
    if threshold <= 0:
        return False
    session = re.sub(r"[^A-Za-z0-9_-]", "", str(data.get("session_id") or ""))[:36]
    if not session or cfg is None:
        return False
    try:
        is_remote = any(re.search(p, command) for p in patterns)
        state = cfg.root / "log" / "ctxguard"
        state.mkdir(parents=True, exist_ok=True)
        marker = state / ("remote-" + session)
        if not is_remote:
            # A local command between two remote ones means the sequence was not
            # a burst that could have been one payload.
            if marker.is_file():
                marker.unlink()
            return False
        run = (int(marker.read_text().strip()) if marker.is_file() else 0) + 1
        if run >= threshold:
            marker.write_text("0")
            return True
        marker.write_text(str(run))
    except Exception:
        return False
    return False


SLOW_CACHE_AGE = 3600      # rebuild the shape/duration table at most hourly
SLOW_MIN_RUNS = 3          # never judge a command on one unlucky sample


def slow_shapes(cfg):
    """Median duration per command shape, from this project's own friction log.

    The log already records what every Bash call cost, so whether a command is
    slow is a question with a MEASURED answer rather than a guess from reading
    it. Reparsing the whole log on every call would be its own cost, so the
    table is cached and rebuilt at most hourly, and it only keeps shapes that
    are actually slow, which makes it small.

    MEDIAN, not mean: one 400 s outlier should not make an ordinarily fast
    command look slow, and a handful of samples is all there ever is.
    """
    log = cfg.root / "log" / "friction" / "commands.jsonl"
    if not log.is_file():
        return {}
    cache = cfg.root / "log" / "friction" / "slow-shapes.json"
    try:
        if cache.is_file() and (time.time() - cache.stat().st_mtime) < SLOW_CACHE_AGE:
            return json.loads(cache.read_text())
    except Exception:
        pass
    try:
        from friction_impl import shape
    except Exception:
        return {}
    buckets: dict = {}
    try:
        for line in log.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            ms = rec.get("ms")
            cmd = rec.get("cmd")
            if not isinstance(ms, int) or not cmd:
                continue
            buckets.setdefault(shape(cmd), []).append(ms)
    except Exception:
        return {}
    table = {}
    for name, runs in buckets.items():
        if len(runs) < SLOW_MIN_RUNS:
            continue
        runs.sort()
        median = runs[len(runs) // 2]
        if median >= 20000:   # keep anything near the threshold, not just past it
            table[name] = median
    try:
        cache.write_text(json.dumps(table))
    except Exception:
        pass
    return table


def slow_and_foreground(cfg, data, command):
    """(shape, median_seconds) when this command is known slow and is NOT backgrounded."""
    if cfg is None:
        return None
    if (data.get("tool_input") or {}).get("run_in_background"):
        return None
    threshold = int(setting(cfg, "guards.background_seconds", 30)) * 1000
    if threshold <= 0:
        return None
    try:
        table = slow_shapes(cfg)
        if not table:
            return None
        from friction_impl import shape
        name = shape(command)
        median = table.get(name)
        if median is None or median < threshold:
            return None
        return name, median / 1000.0
    except Exception:
        return None


def hook_bash() -> None:
    """Refuse the two Bash shapes that cost the most measured time.

    Neither is a style preference. A hand-rolled polling loop re-implements
    something the harness already does for free, and an interpreter heredoc
    costs several times a plain command in tokens, cannot be re-run or edited
    incrementally, and its quoting is a large source of dead-end failures.

    What it does NOT touch: short heredocs, because a three-line one-liner is
    cheaper than creating a file; non-interpreter heredocs like `cat <<EOF`,
    because writing a small config inline is not the failure being fixed; and a
    loop with no sleep, because that is just a loop.
    """
    data = payload()
    cfg = config()
    if not enabled(cfg):
        return
    command = (data.get("tool_input") or {}).get("command")
    if not command:
        return

    # The top-level text has its quoted spans blanked, so a command that merely
    # CONTAINS the shape as a string is not the shape. Anything genuinely hidden
    # in quotes comes back through shell_fragments, which shlex-extracts `sh -c`
    # arguments and yields them whole.
    fragments = [strip_quotes(command)] + list(shell_fragments(command))[1:]
    # Only what the repeater RUNS counts. Measuring the whole command made
    # `sleep 5; ls | xargs rm -f` a polling loop, because a sleep anywhere
    # plus an xargs anywhere satisfied it.
    sleeps_in_repeated = any(_SLEEP_CALL.search(f) for f in fragments[1:])
    polling = (
        any(_WATCH.search(fragment) for fragment in fragments)
        or any(
            _SLEEP_CALL.search(body)
            for fragment in fragments
            for body in _LOOP_BODY.findall(fragment)
        )
        or (any(_REPEATER.search(f) for f in fragments) and sleeps_in_repeated)
    )
    if polling and not _ALLOW_POLL.search(command):
        deny(
            "PreToolUse",
            "This is a hand-rolled polling loop. Nothing needs polling here:\n\n"
            "  - A long command belongs in the background (run_in_background: true).\n"
            "    You are notified when it exits; do not wait for it, end the turn.\n"
            "  - A background task's output file is readable at any time, no loop.\n\n"
            "If this really is a paced retry against something external that nothing\n"
            "will notify you about, prefix the command with ALLOW_POLL=1.",
        )
        return

    limit = int(setting(cfg, "guards.heredoc_lines", 25))
    found = _HEREDOC.search(command)
    # `if found` HAS TO COME FIRST. It used to sit on the line below, after the
    # match had already been dereferenced, so every command WITHOUT a heredoc
    # raised AttributeError here. main() swallows that to keep the promise at
    # rule 1, so the failure was invisible: nothing was being denied for those
    # commands anyway. What it silently did was abort hook_bash at this line, so
    # any check added after it could never run, whatever it was.
    if found:
        # Column 0, the way bash requires it. `<<-` is the exception and
        # allows leading TABS only, never spaces. An indented terminator does
        # not close the heredoc, so counting its lines measured a command that
        # would not have run.
        dash = found.group(0).find("<<-") >= 0
        lead = r"[\t]*" if dash else ""
        terminated = re.search(
            rf"^{lead}{re.escape(found.group(1))}[ \t]*$", command, re.M
        )
    else:
        terminated = None
    if terminated:
        lines = command.count("\n") + 1
        if lines > limit:
            scratch = setting(cfg, "promote.scratch", "tools/scratch")
            deny(
                "PreToolUse",
                f"This is a {lines}-line interpreter heredoc, over the {limit}-line limit.\n\n"
                "A heredoc costs several times a plain command in tokens, cannot be\n"
                "re-run or edited incrementally, and its quoting is the largest single\n"
                "source of dead-end failures in the friction log.\n\n"
                "Write it to a file and run it by path:\n"
                f"  Write  {scratch}/<name>.py     (gitignored, persistent, Edit-able after)\n"
                f"  Bash   python3 {scratch}/<name>.py\n\n"
                f"If it earns a permanent place: promote {scratch}/<name>.py\n\n"
                f"Short heredocs are still fine, this only fires past {limit} lines.",
            )

    # Last, and only if nothing above refused: emit() keeps the first reply, so a
    # denial that already fired is not overwritten by a tip.
    #
    # This one first, because it is the more specific signal: "this exact command has
    # taken 44 s the last five times" beats "you are making a lot of remote calls".
    known_slow = slow_and_foreground(cfg, data, command)
    if known_slow:
        name, seconds = known_slow
        advise(
            "PreToolUse",
            f"slow command: {name} has been taking about {seconds:.0f}s",
            f"`{name}` has a median of about {seconds:.0f}s in this project's own\n"
            "friction log, and this call is in the foreground, so the session blocks\n"
            "for all of it and cannot do anything else meanwhile.\n\n"
            "Prefer run_in_background: true. The harness notifies you when it exits,\n"
            "so there is nothing to wait for and nothing to poll: end the turn, and\n"
            "read its output file whenever the notification arrives. Work that does\n"
            "not depend on the result can carry on in the meantime.\n\n"
            "Keep it in the foreground when you genuinely cannot continue without the\n"
            "answer, or when the command needs to stay attached to this terminal.\n"
            "This is measured from past runs, not a rule, and the estimate can be\n"
            "wrong for an unusual invocation.",
        )
    elif remote_burst(cfg, data, command):
        advise(
            "PreToolUse",
            "batching advisory: several remote calls in a row",
            "That is several back-to-back remote calls with nothing local between them.\n\n"
            "Each one pays a fixed toll before it does any work: a process launch, a\n"
            "connection, an authentication. Paying it once for ten questions is one\n"
            "toll; paying it ten times is ten, and connection reuse often cannot help,\n"
            "on Windows the ssh client cannot multiplex at all.\n\n"
            "If the next few commands do not depend on this one's OUTPUT, send them as\n"
            "one payload and read one answer:\n"
            "  ssh HOST 'bash -s' < script.sh      # nothing re-parses the script\n"
            "  rrun -s bash HOST -c '...'          # same, base64, safe for any quoting\n\n"
            "Ignore this when each step genuinely needs the previous result. This is a\n"
            "cost signal, not an instruction, and it will not fire again until the next\n"
            "run of back-to-back remote calls.",
        )


# --- 3. depth guard -------------------------------------------------------


def hook_depth() -> None:
    """Report context depth, once per threshold crossed.

    Every request re-reads the whole window, so cost tracks depth. Yet depth is
    invisible: nothing reports it until auto-compaction trips near the ceiling.
    This tells the agent, which is the half that decides whether to open a
    subagent or start a fresh session.

    It warns on CROSSINGS, not on every prompt. A hook that adds tokens to
    complain about tokens must be rare or it becomes the thing it warns about.
    """
    data = payload()
    cfg = config()
    if cfg is None or not enabled(cfg):
        return
    transcript = data.get("transcript_path")
    session = re.sub(r"[^A-Za-z0-9_-]", "", str(data.get("session_id") or ""))
    if not transcript or not session or not Path(transcript).is_file():
        return

    # These transcripts reach many megabytes and this runs on every prompt, so
    # read a bounded tail rather than the whole file.
    try:
        with open(transcript, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 400_000))
            tail = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return

    depth = 0
    for line in tail.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except Exception:
            continue
        if record.get("type") != "assistant":
            continue
        usage = (record.get("message") or {}).get("usage")
        if not usage:
            continue
        total = (
            (usage.get("cache_read_input_tokens") or 0)
            + (usage.get("cache_creation_input_tokens") or 0)
            + (usage.get("input_tokens") or 0)
        )
        if total > 0:
            depth = total
    if depth <= 0:
        return

    tiers = [int(t) for t in setting(cfg, "guards.depth_tiers", [300, 500, 700])]
    state = cfg.root / "log" / "ctxguard"
    try:
        state.mkdir(parents=True, exist_ok=True)
        marker = state / session
        seen = int(marker.read_text().strip()) if marker.is_file() else 0
    except Exception:
        return

    crossed = max((t for t in tiers if depth >= t * 1000 and t > seen), default=0)
    if not crossed:
        return
    try:
        marker.write_text(str(crossed))
        cutoff = time.time() - 14 * 86400
        for stale in state.iterdir():
            if stale.is_file() and stale.stat().st_mtime < cutoff:
                stale.unlink()
    except Exception:
        pass

    thousands = depth // 1000
    emit(
        {
            "hookSpecificOutput": {
                "hookEventName": "UserPromptSubmit",
                "additionalContext": (
                    f"Context is now ~{thousands}k tokens. Every tool call from here "
                    f"re-reads that whole window, so each one costs roughly {thousands}k "
                    "cache-read regardless of what it returns.\n\n"
                    "Worth considering before the next chunk of work:\n"
                    "  - Is this a new topic? A fresh session starts far shallower.\n"
                    "  - Bulk reading or searching? A subagent runs in its own window.\n"
                    "  - Staying here is fine if the accumulated context is what the work\n"
                    "    needs. This is a cost signal, not an instruction, and it will not\n"
                    "    repeat until the next tier."
                ),
            }
        }
    )


# --- 4 and 5. friction recorder -------------------------------------------


LOG_GITIGNORE = """# Written by claude-kit's hooks. NEVER COMMIT THIS.
#
# commands.jsonl records the first 400 characters of every Bash command run in
# this project, and 200 characters of any error. That routinely captures tokens
# passed on a command line, Authorization headers, and absolute home paths.
*
"""


def friction_dir(cfg) -> Path | None:
    """The log directory, created with a self-ignoring .gitignore.

    The log holds every command a session ran, which is exactly the kind of
    thing a routine `git add -A` sweeps up. Writing the ignore file at the same
    moment the directory appears is the only point at which nobody has to
    remember anything.
    """
    if cfg is None or not enabled(cfg):
        return None
    directory = cfg.root / "log" / "friction"
    try:
        if not directory.exists():
            directory.mkdir(parents=True, exist_ok=True)
        ignore = cfg.root / "log" / ".gitignore"
        if not ignore.exists():
            ignore.write_text(LOG_GITIGNORE, encoding="utf-8", newline="\n")
    except OSError:
        return None
    return directory


ORPHAN_HOURS = 6


def sweep_orphans(directory: Path) -> None:
    """Record the calls that started and never finished.

    A pre-hook stamp is consumed by the matching post-hook. When a command is
    killed, times out, or the session dies under it, that post-hook never runs
    and the stamp is simply orphaned, so THE CALL IS NEVER RECORDED AT ALL.

    That is the wrong way round: a command that hung is the most interesting
    thing this log could tell anyone, and it was the one thing it threw away. On
    the project this was found on, 21 stamps had accumulated over two days while
    a four minute `git push` hang went entirely unmeasured.

    The threshold is generous on purpose. A foreground call cannot exceed the
    harness ceiling of ten minutes, but a backgrounded one can run for hours, and
    flushing a still-live call would both invent a failure and double count it
    when the real post-hook arrives. Six hours is comfortably past either.
    """
    pending = directory / ".pending"
    if not pending.is_dir():
        return
    cutoff = int(time.time() * 1000) - ORPHAN_HOURS * 3600 * 1000
    records = []
    for stamp in pending.iterdir():
        try:
            if not stamp.is_file():
                continue
            parts = stamp.read_text().split("\n", 1)
            started = int(parts[0].strip())
            if started > cutoff:
                continue
            records.append({
                "ts": datetime.datetime.now(datetime.timezone.utc)
                      .strftime("%Y-%m-%dT%H:%M:%SZ"),
                "session": "",
                "ok": False,
                # UNKNOWN, and it has to stay unknown. The obvious thing is to record the age
                # of the stamp, and that number is not a duration: it is how long the stamp SAT
                # THERE, which for one abandoned two days ago is two days. Written that way, a
                # handful of orphans reported 689 hours between them and became the largest entry
                # in the report, burying every real one. `aggregate` skips a non-int ms, so a null
                # here still counts as a call and as a failure while contributing no time at all,
                # which is exactly the truth: it started, it never finished, nobody knows for how
                # long it ran.
                "ms": None,
                "cmd": (parts[1] if len(parts) > 1 else "")[:400],
                "err": "no completion recorded (killed, timed out, or interrupted)",
            })
            stamp.unlink()
        except Exception:
            # A malformed or vanished stamp must never break the call being made.
            try:
                stamp.unlink()
            except Exception:
                pass
    if not records:
        return
    try:
        with open(directory / "commands.jsonl", "a", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record) + "\n")
    except Exception:
        pass


def hook_friction_pre() -> None:
    """Stamp a start time and the command, keyed by the tool call id."""
    data = payload()
    cfg = config()
    directory = friction_dir(cfg)
    if directory is None:
        return
    call_id = re.sub(r"[^A-Za-z0-9_-]", "", str(data.get("tool_use_id") or ""))
    if not call_id:
        return
    try:
        pending = directory / ".pending"
        pending.mkdir(parents=True, exist_ok=True)
        # The command goes in the stamp so that a call which never completes can
        # still be reported as something more useful than an anonymous orphan.
        command = str((data.get("tool_input") or {}).get("command") or "")[:400]
        (pending / call_id).write_text(
            "%d\n%s" % (int(time.time() * 1000), command)
        )
        sweep_orphans(directory)
    except Exception:
        pass


def hook_friction_post(outcome: str) -> None:
    """Append one record: what ran, how long it took, and whether it worked."""
    data = payload()
    cfg = config()
    directory = friction_dir(cfg)
    if directory is None:
        return
    command = (data.get("tool_input") or {}).get("command")
    if not command:
        return

    elapsed = None
    call_id = re.sub(r"[^A-Za-z0-9_-]", "", str(data.get("tool_use_id") or ""))
    if call_id:
        stamp = directory / ".pending" / call_id
        try:
            # First line is the start time; the command follows it, so that an
            # orphaned stamp can still be reported. See sweep_orphans.
            started = int(stamp.read_text().split("\n", 1)[0].strip())
            stamp.unlink()
            delta = int(time.time() * 1000) - started
            # A clock jump would otherwise record a nonsense duration.
            elapsed = delta if delta >= 0 else None
        except Exception:
            elapsed = None

    response = data.get("tool_response")
    error = ""
    if isinstance(response, dict):
        error = str(response.get("stderr") or response.get("error") or "")
    elif isinstance(response, str):
        error = response if outcome != "ok" else ""

    record = {
        # Truncated: an inlined script can be tens of kilobytes, and the whole
        # point of this log is that it stays cheap to keep forever.
        "ts": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "session": str(data.get("session_id") or "")[:36],
        "ok": outcome == "ok",
        "ms": elapsed,
        "cmd": str(command)[:400],
        "err": error[:200],
    }
    try:
        directory.mkdir(parents=True, exist_ok=True)
        with open(directory / "commands.jsonl", "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record) + "\n")
    except Exception:
        pass


# --- 6. session start -----------------------------------------------------


def hook_session_start() -> None:
    """Open every session with the same orientation.

    On exit 0 this hook's stdout joins the session context, so whatever it
    prints is what the session knows before it reads anything. That makes it
    both the highest-leverage and the most expensive few lines in the harness:
    it is paid on every session, forever. The brief is capped for that reason.

    Skipped after a compaction: the context was just summarised from a session
    that already had the brief, so re-injecting it is pure cost.
    """
    data = payload()
    if data.get("source") == "compact":
        return
    cfg = config()
    if cfg is None or not enabled(cfg):
        return
    brief = Path(__file__).resolve().parent.parent / "bin" / "brief"
    if not brief.is_file():
        return
    try:
        result = subprocess.run(
            [sys.executable, str(brief)],
            capture_output=True,
            text=True,
            cwd=str(cfg.root),
            timeout=20,
        )
    except Exception:
        return
    if "BRIEF OK" in (result.stdout or ""):
        sys.stdout.write(result.stdout)


# --- dispatch -------------------------------------------------------------

ACTIONS = {
    "read": hook_read,
    "bash": hook_bash,
    "depth": hook_depth,
    "friction-pre": hook_friction_pre,
    "friction-post": lambda: hook_friction_post("ok"),
    "friction-fail": lambda: hook_friction_post("fail"),
    "session-start": hook_session_start,
}


def main() -> int:
    if len(sys.argv) < 2:
        return 0
    action = ACTIONS.get(sys.argv[1])
    if action is None:
        return 0
    try:
        action()
    except Exception:
        # Rule 1. Whatever went wrong, it must not block the tool call.
        pass
    return 0


if __name__ == "__main__":
    # Not sys.exit. A deferred stdout flush at interpreter shutdown can hit
    # EPIPE, and CPython rewrites the status to 120 - which the harness reads
    # as a hook failure, breaking the fail-open promise at rule 1. Flush by
    # hand, swallow the error, and leave through a door that cannot be
    # rewritten. prompt_shaper.py in the agent-tiers plugin found this first.
    main()
    try:
        sys.stdout.flush()
    except Exception:
        pass
    os._exit(0)
