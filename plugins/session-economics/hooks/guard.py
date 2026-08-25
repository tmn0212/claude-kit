#!/usr/bin/env python3
"""Every hook in the session-economics plugin, behind one subcommand.

One script rather than six, because each hook is thirty lines of policy around
the same payload read, and six copies of that would drift.

    guard.py read           PreToolUse(Read)     refuse an unbounded whole-file read
    guard.py bash           PreToolUse(Bash)     refuse polling loops and long heredocs
    guard.py depth          UserPromptSubmit     report context depth on a crossing
    guard.py friction-pre   PreToolUse(Bash)     stamp a start time
    guard.py friction-post  PostToolUse(Bash)    record the outcome
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


def emit(obj) -> None:
    try:
        sys.stdout.write(json.dumps(obj))
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

    # An explicit offset or limit is a deliberate, bounded read. Always allow it.
    tool_input = data.get("tool_input") or {}
    if tool_input.get("offset") is not None or tool_input.get("limit") is not None:
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

    threshold = int(setting(cfg, "guards.read_bytes", 24000))
    try:
        size = Path(path).stat().st_size
    except OSError:
        return
    if size <= threshold:
        return

    subject = Path(path).stem
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

_POLL = re.compile(r"\b(until|while|for)\b[\s\S]{0,400}?\bsleep\b", re.I)
_HEREDOC = re.compile(r"\b(python3?|node|perl|ruby|bun|deno)\b[^\n]*<<")


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

    if "ALLOW_POLL=1" not in command and _POLL.search(command):
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
    if _HEREDOC.search(command):
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


def friction_dir(cfg) -> Path | None:
    if cfg is None or not enabled(cfg):
        return None
    return cfg.root / "log" / "friction"


def hook_friction_pre() -> None:
    """Stamp a start time, keyed by the tool call id."""
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
        (pending / call_id).write_text(str(int(time.time() * 1000)))
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
            started = int(stamp.read_text().strip())
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
    sys.exit(main())
