#!/usr/bin/env python3
"""Report where agent sessions spend TOKENS, the way friction.py reports time.

Reads the harness's own transcripts under ~/.claude/projects/<repo>/. Those record
every tool call and its result, so this derives rather than asserts: nothing here
estimates a cost that was not measured.

    tokencost              full report
    tokencost --brief      the few lines tools/brief.sh embeds
    tokencost --top 30     deeper list
    tokencost --dupes      redundant re-reads only
    tokencost --sessions   per-session API usage
    tokencost --subagents  what delegation costs, per run and per call

TOKENCOST OK on stdout is the success signal. Grep for the line, not exit code 0.

## Why amortised cost, and not result size

A tool result is not paid once. It stays in the context window and is re-read on
every following request in that session, which is what the API bills as cache-read.
So the real cost of a result is its size TIMES the number of requests after it:

    a 10k-token result at turn 5 of a 500-turn session is re-read 495 times

Ranking by raw result size therefore under-counts early bulk dumps and over-counts
late ones. This tool ranks by the product, and prints the measured cache-read total
alongside so the model can be checked against the bill.

## Content is the smaller half. Depth is the bigger half

The amortised table ranks CONTENT: which results were big and early. Measured over
18 real sessions, that is the smaller term. Sessions here run long enough that the
window saturates, so the marginal cost of one more tool call is close to the whole
window (mean 431k) no matter what it returns:

    86% of the bill is paid above 300k of context
    request 250-400 of a session costs 4.8x request 1-50

So the two headline numbers are the depth histogram and `tool calls per reply`
(measured 1.13: 90% of replies issue exactly one call, and each reply
pays a full context re-read). Batching independent calls and keeping sessions
shallow both beat any amount of trimming individual results.

## Count each reply once

The harness writes one JSONL record per content block, and every record repeats the
same usage counters. Summing them all double-counts by the mean blocks-per-reply,
which is ~2x here. `scan` dedupes on message id; before it did, this tool reported
4.8B over 11623 requests where the truth was 2.5B over 5784.

## The 1.85 s call penalty

Measured on this machine, reproducibly:

    grep -c . README.md          91 ms      kb stats      90 ms
    python3 -c "print(1)"      1843 ms      grep -c . /etc/hostname 1815 ms

A no-op `python3 - <<'PY'` heredoc reported `internal_elapsed=0.0000s` while the
harness billed 1900 ms. It is not the sandbox (disabling it does not help), not the
hooks (5 jq spawns = 17 ms) and not bubblewrap (2 ms). Invoking a repo script by
path is fast; inline interpreter code and out-of-project absolute paths are not.

The fix is this file's own existence: an analysis that runs more than once belongs
in tools/ where it costs 90 ms, not in a heredoc that costs 1.85 s every time.
`--penalty` reports what the current friction log is still paying for that.
"""

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

from kit_config import Config

_CFG = Config.load()

ROOT = _CFG.root

# The harness encodes the project path by replacing every separator and '_'
# with '-'. The backslash matters: on Windows str(ROOT) contains no forward
# slashes at all, so a class of just [/_] leaves the path unrecognisable and
# the transcript directory is never found.
TRANSCRIPTS = Path.home() / ".claude" / "projects" / re.sub(r"[/\\_]", "-", str(ROOT))

# Programs that do nothing on their own: the subcommand is the real identity.
MULTIPLEXERS = {
    "git", "npm", "npx", "pnpm", "yarn", "cargo", "docker", "pip", "pip3",
    "python", "python3", "go", "gh", "cmake", "make", "claude",
} | set(_CFG.get("economics.multiplexers", []))

# Shell prologue and pure-output builtins; bucketing on these would make the whole
# report say "you spend all your time running echo".
TRIVIAL = {
    "set", "cd", "export", "echo", "printf", "true", "false", ":", "source", ".",
    "shopt", "umask", "unset", "alias", "pwd", "read",
}

_ENV_ASSIGN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=\S*$")
_SPLIT = re.compile(r"(?:\|\||&&|\||;|\n)")

# Commands that pay the ~1.85 s harness penalty: inline interpreter code, or a path
# outside the project. Kept deliberately narrow so the number is a floor, not a guess.
_INLINE = re.compile(r"(?:python3?|perl|ruby|node)\s+(?:-\S+\s+)*(?:-c\b|-\s*<<)|<<\s*['\"]?(?:PY|EOF|PYEOF)")
_OUTSIDE = re.compile(r"(?<![\w/])/(?:usr|etc|bin|sbin|opt|dev|proc|sys|var|tmp)\b")
PENALTY_S = float(_CFG.get("economics.penalty_seconds", 1.85))


def bash_identity(cmd):
    """(program, subcommand-qualified name) for the first meaningful segment."""
    for segment in _SPLIT.split(cmd.strip()):
        parts = segment.strip().split()
        while parts and _ENV_ASSIGN.match(parts[0]):
            parts.pop(0)
        if not parts:
            continue
        prog = parts[0]
        base = prog.rsplit("/", 1)[-1]
        if base in TRIVIAL:
            continue
        # `timeout 900 ./x.sh` is about x.sh, not about timeout.
        if base == "timeout":
            parts = parts[2:] if len(parts) > 2 else parts[1:]
            if not parts:
                continue
            prog = parts[0]
            base = prog.rsplit("/", 1)[-1]
        if base in MULTIPLEXERS:
            candidates = [t for t in parts[1:] if not t.startswith("-")]
            bare = [t for t in candidates
                    if "=" not in t and not t[:1].isdigit()]
            pick = bare[0] if bare else (candidates[0] if candidates else None)
            if pick:
                return f"{prog} {pick}"
        return prog
    return cmd.split()[0] if cmd.split() else "?"


def result_text(content):
    """Flatten a tool_result body to the text that entered the context window."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for block in content:
            if not isinstance(block, dict):
                out.append(str(block))
            elif block.get("type") == "text":
                out.append(block.get("text", ""))
            elif block.get("type") == "image":
                # An image is not text; ~1000 tokens is the usual screenshot cost.
                out.append("x" * 4000)
            else:
                out.append(json.dumps(block))
        return "\n".join(out)
    return json.dumps(content) if isinstance(content, dict) else str(content)


def identity(name, inp):
    """One bucket label per tool call, chosen so the label names the fix."""
    if name == "Bash":
        return bash_identity(inp.get("command", ""))
    if name in ("Read", "Edit", "Write", "NotebookEdit"):
        path = inp.get("file_path", "")
        return f"{name}:{Path(path).suffix or Path(path).name or 'noext'}"
    if name in ("Task", "Agent"):
        return f"Agent:{inp.get('subagent_type', '?')}"
    return name


def scan(session_filter=None):
    """One pass over every transcript. Returns (calls, api, sessions, depth).

    calls: list of (identity, tokens, remaining_requests, target, session)
    api:   summed usage counters
    depth: context size of every request, for the depth histogram
    """
    if not TRANSCRIPTS.is_dir():
        print(f"no transcripts at {TRANSCRIPTS}", file=sys.stderr)
        return None, None, None, None

    files = sorted(TRANSCRIPTS.glob("*.jsonl"))
    if session_filter == "LAST" and files:
        files = [max(files, key=lambda f: f.stat().st_mtime)]
    elif session_filter:
        files = [f for f in files if f.stem.startswith(session_filter)]

    calls = []
    api = defaultdict(int)
    sessions = []
    depth = []                       # context size of every request, all sessions
    per_reply = defaultdict(int)     # message id -> tool_use blocks in that reply
    for path in files:
        uses = {}
        events = []
        usage = defaultdict(int)
        seen_msgs = set()
        with open(path, errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                kind = rec.get("type")
                if kind == "assistant":
                    msg = rec.get("message", {}) or {}
                    # The harness writes one JSONL record per content block, and
                    # every one of them carries the SAME usage counters. A reply
                    # with thinking + text + two tool_use blocks lands as four
                    # records. Counting them all inflates requests and cache_read
                    # by the mean blocks-per-reply (~2x here) and skews the
                    # "requests that follow" weight below. Count each reply once.
                    mid = msg.get("id")
                    if mid is None or mid not in seen_msgs:
                        seen_msgs.add(mid)
                        events.append(None)      # marks one request
                        use = msg.get("usage") or {}
                        usage["out"] += use.get("output_tokens", 0)
                        usage["cache_create"] += use.get("cache_creation_input_tokens", 0)
                        usage["cache_read"] += use.get("cache_read_input_tokens", 0)
                        usage["requests"] += 1
                        ctx = (use.get("cache_read_input_tokens", 0)
                               + use.get("cache_creation_input_tokens", 0)
                               + use.get("input_tokens", 0))
                        if ctx > 0:
                            depth.append(ctx)
                    for block in msg.get("content", []) or []:
                        if isinstance(block, dict) and block.get("type") == "tool_use":
                            uses[block["id"]] = (block.get("name"),
                                                 block.get("input", {}) or {})
                            per_reply[mid] += 1
                elif kind == "user":
                    content = rec.get("message", {}).get("content")
                    if not isinstance(content, list):
                        continue
                    for block in content:
                        if not (isinstance(block, dict)
                                and block.get("type") == "tool_result"):
                            continue
                        use = uses.get(block.get("tool_use_id"))
                        if not use:
                            continue
                        name, inp = use
                        events.append((name, inp,
                                       len(result_text(block.get("content"))) // 4))

        total = sum(1 for e in events if e is None)
        seen = 0
        for event in events:
            if event is None:
                seen += 1
                continue
            name, inp, tokens = event
            target = (inp.get("file_path") or inp.get("command")
                      or inp.get("pattern") or "")
            calls.append((identity(name, inp), tokens, max(0, total - seen),
                          target, path.stem[:8], name))
        for key, value in usage.items():
            api[key] += value
        if usage["requests"]:
            sessions.append((path.stem[:8], dict(usage)))
    api["tool_uses"] = sum(per_reply.values())
    api["tool_replies"] = len(per_reply)
    return calls, api, sessions, depth


def human(n):
    for unit, scale in (("B", 1e9), ("M", 1e6), ("k", 1e3)):
        if abs(n) >= scale:
            return f"{n / scale:.1f}{unit}"
    return str(int(n))


def depth_report(depth, requests):
    """Where the bill is actually paid: context size at the moment of each request.

    The amortised table above ranks CONTENT. This ranks DEPTH, and at this
    project's operating point depth dominates: sessions run so long that context
    saturates, so the marginal cost of a tool call is the whole window, near
    enough regardless of how big that call's own result is.
    """
    if not depth:
        return
    mean = sum(depth) / len(depth)
    print(f"\nWhere the bill is paid   (context size when the request was made)")
    print(f"{'context':>14}{'requests':>10}{'cache-read':>12}{'share':>8}{'cumulative':>12}")
    buckets = defaultdict(lambda: [0, 0])
    for ctx in depth:
        row = buckets[min(int(ctx // 100_000) * 100, 900)]
        row[0] += 1
        row[1] += ctx
    total = sum(r[1] for r in buckets.values())
    cum = 0
    for lo in sorted(buckets):
        n, tok = buckets[lo]
        cum += tok
        print(f"{lo:>8}-{lo + 100}k{n:>10}{human(tok):>12}"
              f"{100 * tok / total:>7.1f}%{100 * cum / total:>11.1f}%")
    print(f"\n  mean context per request {human(mean)}. That is the marginal cost of")
    print(f"  one more tool call, whatever it returns. Halving the call count for a")
    print(f"  piece of work halves the bill for it; shrinking the result barely moves it.")


def subagent_report():
    """What delegation actually costs, per run and per unit of work.

    A subagent starts on a fresh window, so its tool calls are billed at a much
    shallower context than the same calls made in a saturated main session. That
    is the whole economic case for delegating, and it is worth checking rather
    than asserting: the run total and the per-call rate point opposite ways.
    """
    runs = []
    for path in sorted(TRANSCRIPTS.glob("*/subagents/*.jsonl")):
        seen, ctxs, tools = set(), [], 0
        with open(path, errors="replace") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue
                if rec.get("type") != "assistant":
                    continue
                msg = rec.get("message", {}) or {}
                for block in msg.get("content", []) or []:
                    if isinstance(block, dict) and block.get("type") == "tool_use":
                        tools += 1
                mid = msg.get("id")
                if not mid or mid in seen:
                    continue
                seen.add(mid)
                use = msg.get("usage") or {}
                ctx = (use.get("cache_read_input_tokens", 0)
                       + use.get("cache_creation_input_tokens", 0)
                       + use.get("input_tokens", 0))
                if ctx > 0:
                    ctxs.append(ctx)
        if ctxs:
            runs.append((sum(ctxs), len(ctxs), tools, max(ctxs)))
    if not runs:
        print("no subagent transcripts recorded yet")
        return
    runs.sort(reverse=True)
    total = sum(r[0] for r in runs)
    reqs = sum(r[1] for r in runs)
    tools = sum(r[2] for r in runs)
    print(f"SUBAGENT COST   {len(runs)} runs")
    print(f"  total cache-read       {human(total)}")
    print(f"  requests               {reqs}, tool calls {tools} "
          f"({tools / len(runs):.0f} per run)")
    print(f"  mean context/request   {human(total / reqs)}")
    print(f"  mean cost per run      {human(total / len(runs))}")
    print(f"  median cost per run    {human(sorted(r[0] for r in runs)[len(runs) // 2])}")
    print(f"  cost per tool call     {human(total / max(1, tools))}")
    print(f"\n  Most expensive runs (cache-read, requests, tool calls, peak context)")
    for r in runs[:8]:
        print(f"    {human(r[0]):>9}{r[1]:>6} req{r[2]:>6} tools   peak {human(r[3])}")
    print(f"\n  Delegation is cheap per unit of work and expensive per run: a fresh")
    print(f"  window bills each call far below a saturated one, but a loose brief")
    print(f"  spends dozens of calls rediscovering the tree. Bound the brief.")


def report(calls, api, top, depth=None):
    own = sum(c[1] for c in calls)
    amort = sum(c[1] * c[2] for c in calls)
    print(f"TOKENCOST  {len(calls)} tool calls, {human(own)} tokens of tool output")
    print(f"           {human(api['cache_read'])} cache-read tokens billed over "
          f"{api['requests']} requests "
          f"({human(api['cache_read'] / max(1, api['requests']))} avg context)")
    print(f"           {human(api['cache_create'])} cache-creation, "
          f"{human(api['out'])} output")
    if api.get("tool_replies"):
        par = api["tool_uses"] / api["tool_replies"]
        print(f"           {par:.2f} tool calls per reply that used tools "
              f"({api['tool_uses']} calls in {api['tool_replies']} replies)."
              f" Every reply costs a full context re-read,")
        print(f"           so batching independent calls into one reply is the "
              f"cheapest saving available.")

    agg = defaultdict(lambda: [0, 0, 0, 0, ""])
    for ident, tokens, remaining, target, _sess, _name in calls:
        row = agg[ident]
        row[0] += 1
        row[1] += tokens
        row[2] += tokens * remaining
        if tokens > row[3]:
            row[3], row[4] = tokens, target[:70]

    print(f"\nBy amortised context cost   (own tokens x requests that follow)")
    print(f"{'identity':<40}{'calls':>6}{'own':>9}{'amortised':>11}{'%':>6}{'avg':>7}")
    for ident, row in sorted(agg.items(), key=lambda kv: -kv[1][2])[:top]:
        share = 100 * row[2] / amort if amort else 0
        print(f"{ident[:39]:<40}{row[0]:>6}{row[1]:>9,}{human(row[2]):>11}"
              f"{share:>6.1f}{row[1] // max(1, row[0]):>7}")

    print(f"\nBiggest single results   (one-shot context dumps)")
    for ident, row in sorted(agg.items(), key=lambda kv: -kv[1][3])[:8]:
        print(f"  {row[3]:>7} tok  {ident:<24} {row[4]}")
    depth_report(depth, api["requests"])
    return amort


def dupes(calls, top):
    """Files pulled into context repeatedly inside one session."""
    per = defaultdict(lambda: [0, 0])
    for ident, tokens, _rem, target, sess, name in calls:
        if tokens < 40 or name not in ("Read", "Bash"):
            continue
        if name == "Bash":
            if not re.match(r"\s*(sed|cat|head|tail|awk)\b", target):
                continue
            found = re.findall(r"[\w./-]*\.(?:md|qmd|c|h|py|sh|json|toml|tsv)\b", target)
        else:
            found = [target]
        for item in found:
            item = item.replace(str(ROOT) + "/", "")
            if not item or item.startswith(("/tmp", str(Path.home() / ".claude"))):
                continue
            entry = per[(sess, item)]
            entry[0] += 1
            entry[1] += tokens

    rows = [(tok - tok // n, n, tok, sess, path)
            for (sess, path), (n, tok) in per.items() if n >= 3]
    rows.sort(reverse=True)
    wasted = sum(tok - tok // n for (n, tok) in
                 ((v[0], v[1]) for v in per.values()) if n >= 2)
    print(f"\nRedundant re-reads   {human(wasted)} tokens spent re-reading files "
          f"already in context")
    print(f"{'wasted':>8}{'reads':>7}{'total':>8}  {'session':<10}file")
    for waste, n, tok, sess, path in rows[:top]:
        print(f"{waste:>8}{n:>7}{tok:>8}  {sess:<10}{path}")
    return wasted


def penalty():
    """What the friction log is still paying for inline interpreters."""
    log = ROOT / "log" / "friction" / "commands.jsonl"
    if not log.exists():
        return
    inline = out = total = 0
    inline_ms = 0
    with open(log, errors="replace") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            total += 1
            cmd = rec.get("cmd", "")
            if _INLINE.search(cmd):
                inline += 1
                inline_ms += rec.get("ms") or 0
            elif _OUTSIDE.search(cmd):
                out += 1
    if not total:
        return
    print(f"\nCall penalty   ~{PENALTY_S}s per call for inline interpreters and "
          f"out-of-project paths")
    print(f"  inline interpreter (python -c, heredoc)  {inline:>5} calls, "
          f"{inline_ms / 60000:>5.0f} min wall, {inline * PENALTY_S / 60:>4.0f} min penalty")
    print(f"  out-of-project absolute path             {out:>5} calls, "
          f"{out * PENALTY_S / 60:>4.0f} min penalty")
    print(f"  a repo script under tools/ costs ~90 ms instead. Promote, "
          f"don't re-derive: promote")


def sessions_report(sessions):
    print(f"\n{'session':<10}{'requests':>9}{'output':>10}{'cache-create':>14}"
          f"{'cache-read':>12}")
    for sess, use in sorted(sessions, key=lambda s: -s[1]["cache_read"]):
        print(f"{sess:<10}{use['requests']:>9}{use['out']:>10,}"
              f"{use['cache_create']:>14,}{human(use['cache_read']):>12}")


def main():
    ap = argparse.ArgumentParser(add_help=False)
    ap.add_argument("--top", type=int, default=20)
    ap.add_argument("--brief", action="store_true")
    ap.add_argument("--dupes", action="store_true")
    ap.add_argument("--sessions", action="store_true")
    ap.add_argument("--subagents", action="store_true")
    ap.add_argument("--session", default=None)
    ap.add_argument("-h", "--help", action="store_true")
    args = ap.parse_args()
    if args.help:
        print(__doc__)
        return 0

    calls, api, sessions, depth = scan(args.session)
    if calls is None:
        # No transcript directory yet. That is the state of every project on
        # its first day, so it is an empty result rather than a failure: a
        # non-zero exit here would read as a broken tool.
        print("no sessions recorded for this project yet")
        print("TOKENCOST OK")
        return 0
    if not calls:
        print("no tool calls recorded yet")
        print("TOKENCOST OK")
        return 0

    if args.brief:
        amort = sum(c[1] * c[2] for c in calls)
        agg = defaultdict(int)
        for ident, tokens, remaining, _t, _s, _n in calls:
            agg[ident] += tokens * remaining
        worst = sorted(agg.items(), key=lambda kv: -kv[1])[:3]
        print("  token sinks: " + ", ".join(
            f"{k} {100 * v / amort:.0f}%" for k, v in worst))
        if depth and api.get("tool_replies"):
            par = api["tool_uses"] / api["tool_replies"]
            deep = 100 * sum(c for c in depth if c > 300_000) / max(1, sum(depth))
            print(f"  context: {human(sum(depth) / len(depth))} mean, "
                  f"{deep:.0f}% of the bill paid above 300k, "
                  f"{par:.2f} tool calls per reply")
        print("TOKENCOST OK")
        return 0

    if args.subagents:
        subagent_report()
        print("\nTOKENCOST OK")
        return 0
    if args.sessions:
        sessions_report(sessions)
        print("\nTOKENCOST OK")
        return 0
    if args.dupes:
        dupes(calls, args.top)
        print("\nTOKENCOST OK")
        return 0

    report(calls, api, args.top, depth)
    dupes(calls, min(args.top, 12))
    penalty()
    print("\nTOKENCOST OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
