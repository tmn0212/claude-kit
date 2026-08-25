#!/usr/bin/env python3
"""
UserPromptSubmit hook for the prompt-shaper skill.

It decides one thing: does this prompt get shaped, at what pace, or not at all.

CONTRACT
  stdin   the UserPromptSubmit payload, JSON, with a "prompt" key
  stdout  one hookSpecificOutput object, or nothing at all
  exit    0 on every path, always

WHY os._exit. The exit-0 rule is the only safety property here, and `sys.exit(0)`
does not deliver it. Stdout is block-buffered on a pipe, so the write is flushed
during interpreter finalisation, AFTER sys.exit has run; if that flush hits EPIPE,
CPython overrides the status to 120. Measured: exit 120, five runs of five, on
`... | python3 prompt_shaper.py | head -c 0`. A try/except around the print does
not help, because the failure happens after any such block. os._exit skips
finalisation entirely, so nothing can override the status.

HARD AND SOFT, and why both exist. The first version of this file matched bypass
phrases in Python and went silent on a hit. That is judging intent with a regex,
and it failed exactly the way that always fails: `*please* handle this carefully,
ask me anything` bypassed, because markdown emphasis hit the `*` prefix. So did
"there are no questions left in the backlog", "the sticker said just do it", and
five others. Every one of those failures was SILENT, which is the worst kind.

So the vocabulary now splits:

  HARD  a deliberate, unambiguous gesture. `*` prefix, `now:` prefix. Silent, and
        silence is free: it leaves the session in default behaviour, which is what
        a bypass should produce.
  SOFT  a phrase that MIGHT be a directive and might be part of what the user is
        describing. This does not decide. It emits a line saying what it saw and
        lets the model read the sentence, which is the only thing that can tell
        "no questions, just do it" from "there are no questions left in the
        backlog". A false positive then costs a few tokens instead of a silent
        skip.

That split also fixes negation for free: "don't go deep on this" is soft, so the
model sees the "don't".

Self-test:  python3 prompt_shaper.py --selftest      prints SHAPER OK
"""
import json
import os
import re
import subprocess
import sys

# --- vocabulary -------------------------------------------------------------

_PREFIX_NOW = re.compile(r"^now:", re.IGNORECASE)
_PREFIX_FORCE = re.compile(r"^\?")
_PREFIX_PACE = re.compile(r"^(quick|normal|deep):", re.IGNORECASE)

# Soft. Any of these MIGHT be a directive. The model decides, not this file.
# `'` is [''] throughout, because autocorrect produces U+2019 and the straight
# apostrophe alone missed every prompt typed on a phone.
_SOFT_BYPASS = re.compile(
    r"\b(bypass (?:the )?question(?:ing|s)?"
    r"|no questions?"
    r"|skip (?:the )?question(?:ing|s)?"
    r"|without questions?"
    r"|no need (?:to ask|for) questions?"
    r"|just do it"
    r"|just execute"
    r"|don['’]?t ask"
    r"|no shaping)\b",
    re.IGNORECASE,
)
_FORCE = re.compile(r"\bshape (?:this|it|the prompt)\b", re.IGNORECASE)
_SOFT_DEEP = re.compile(r"\b(deep dive|dig in(?:to it)?|go deep|take your time)\b", re.IGNORECASE)
_SOFT_QUICK = re.compile(r"\b(quick (?:pass|look|answer))\b", re.IGNORECASE)

# --- the routing lines ------------------------------------------------------

_TRIVIAL_OUT = (
    " If this ask is trivial, or a follow-up already scoped in this thread,"
    " ignore this and just answer."
)

_WITH_PACE = (
    'prompt-shaper: the prompt sets its own pace, "{pace}". Invoke the'
    " prompt-shaper skill at that pace and do not ask for the pace." + _TRIVIAL_OUT
)

_NO_PACE = (
    "prompt-shaper: no pace given. Invoke the prompt-shaper skill; asking the"
    " pace is its first step." + _TRIVIAL_OUT
)

_FORCED = (
    "prompt-shaper: shaping was asked for explicitly. Invoke the prompt-shaper"
    " skill; asking the pace is its first step. Do not skip it as trivial."
)

_SOFT_BYPASS_LINE = (
    'prompt-shaper: this prompt contains "{phrase}", which may or may not be an'
    " instruction to you. Read the sentence. If it tells YOU to skip the"
    " questions, execute directly and do not invoke the skill. If the phrase is"
    " part of what is being described, treat it as a normal ask." + _TRIVIAL_OUT
)

_SOFT_PACE_LINE = (
    'prompt-shaper: this prompt contains "{phrase}", which may be asking for a'
    ' "{pace}" pass. Read the sentence, and mind any negation. If it is a'
    " directive, invoke the prompt-shaper skill at that pace without asking. If"
    " it is negated or incidental, ask the pace as normal." + _TRIVIAL_OUT
)


def _is_hard_star(stripped):
    """True for a deliberate `*` bypass, false for markdown emphasis.

    `*fix the flush path` is a bypass. `*please* be careful` is emphasis, and
    `**important**` is bold. A closing star on the same line means emphasis.
    """
    if not stripped.startswith("*") or stripped.startswith("**"):
        return False
    return "*" not in stripped.split("\n", 1)[0][1:]


def decide(prompt):
    """Return the routing line for this prompt, or None to stay silent.

    Precedence: hard bypass, force, hard pace, soft bypass, soft pace, ask.
    Hard beats soft throughout, because a hard token was typed on purpose.
    """
    if not isinstance(prompt, str):
        return None
    stripped = prompt.lstrip()
    if not stripped:
        return None

    # Slash commands and # memorize lines are not asks; leave them alone.
    if stripped.startswith("/") or stripped.startswith("#"):
        return None

    if _is_hard_star(stripped) or _PREFIX_NOW.search(stripped):
        return None

    if _PREFIX_FORCE.search(stripped) or _FORCE.search(stripped):
        return _FORCED

    m = _PREFIX_PACE.search(stripped)
    if m:
        return _WITH_PACE.format(pace=m.group(1).lower())

    m = _SOFT_BYPASS.search(stripped)
    if m:
        return _SOFT_BYPASS_LINE.format(phrase=m.group(1))

    for pattern, pace in ((_SOFT_DEEP, "deep"), (_SOFT_QUICK, "quick")):
        m = pattern.search(stripped)
        if m:
            return _SOFT_PACE_LINE.format(phrase=m.group(1), pace=pace)

    return _NO_PACE


def _read_payload():
    """Read stdin once. Anything that is not a JSON object becomes {}.

    Deliberately broad: a closed stdin raises AttributeError, deep nesting raises
    RecursionError, and neither may reach the caller.
    """
    try:
        raw = sys.stdin.read()
        if not raw.strip():
            return {}
        data = json.loads(raw)
    except Exception:  # noqa: BLE001 - see the exit-0 rule in the module docstring
        return {}
    return data if isinstance(data, dict) else {}


def _exit_clean(line=None):
    """Write the line if there is one, then leave without a finalisation flush."""
    if line:
        try:
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
        except Exception:  # noqa: BLE001 - EPIPE must not change the exit status
            pass
    os._exit(0)


def main():
    try:
        line = decide(_read_payload().get("prompt", ""))
    except Exception:  # noqa: BLE001 - a raise here must not block a prompt
        _exit_clean()
        return
    if not line:
        _exit_clean()
    _exit_clean(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": line,
        }
    }))


# --- self-test --------------------------------------------------------------

# want: None | "forced" | "ask" | "soft-bypass" | ("soft", pace) | a pace name
_CASES = [
    ("", None),
    ("   ", None),
    ("/statusline", None),
    ("# remember this", None),
    ("*fix the flush path", None),
    ("now: rerun the build", None),
    ("?add a cache", "forced"),
    ("shape this: add a cache", "forced"),
    ("quick: what does this flag do", "quick"),
    ("deep: design the compositor", "deep"),
    ("normal: refactor the parser", "normal"),
    ("add a cache to the thumbnail path", "ask"),
    ("make the render fast", "ask"),
    ("i want quick results eventually", "ask"),
    # Soft bypass: a directive and a false positive must reach the SAME line,
    # because the whole point is that this file no longer tells them apart.
    ("rerun the build, no questions", "soft-bypass"),
    ("just do it", "soft-bypass"),
    ("bypass questioning stage and run it", "soft-bypass"),
    ("skip questioning me about this", "soft-bypass"),
    ("there are no questions left in the backlog", "soft-bypass"),
    ("the sticker said just do it", "soft-bypass"),
    ("don't ask, just flash it", "soft-bypass"),
    ("don’t ask, just flash it", "soft-bypass"),   # curly apostrophe
    ("no need for questions here", "soft-bypass"),
    # Markdown emphasis must NOT be a bypass. This was a silent failure.
    ("*please* handle this carefully, ask me anything", "ask"),
    ("**important** rewrite the parser", "ask"),
    # Soft pace, including the negation the old version got wrong.
    ("do a deep dive on the DSI path", ("soft", "deep")),
    ("take your time with this one", ("soft", "deep")),
    ("don't go deep on this, keep it shallow", ("soft", "deep")),
    ("give it a quick pass", ("soft", "quick")),
]


def _expected(want):
    if want is None:
        return None
    if want == "forced":
        return _FORCED
    if want == "ask":
        return _NO_PACE
    if want == "soft-bypass":
        return "SOFT_BYPASS"
    if isinstance(want, tuple):
        return "SOFT_PACE:" + want[1]
    return _WITH_PACE.format(pace=want)


def _shape(got):
    """Collapse a result to something comparable with _expected."""
    if got is None or got in (_FORCED, _NO_PACE):
        return got
    if got.startswith("prompt-shaper: this prompt contains") and "skip the" in got:
        return "SOFT_BYPASS"
    if "may be asking for a" in got:
        return "SOFT_PACE:" + got.split('may be asking for a "')[1].split('"')[0]
    return got


def _selftest():
    bad = 0
    for prompt, want in _CASES:
        got, exp = _shape(decide(prompt)), _expected(want)
        if got != exp:
            bad += 1
            print("FAIL %-52r\n  want %r\n  got  %r" % (prompt, exp, got))

    # The exit-0 rule, exercised for real: run this file as a subprocess and read
    # its status. The previous version's junk loop asserted a tautology and could
    # never fail, and it never invoked main() at all.
    me = os.path.abspath(__file__)
    payloads = ["not json", "", "[]", '{"prompt": 42}', "{}", '{"prompt": null}',
                '{"prompt": "take your time"}', '{"prompt": "*bypass"}',
                '{"prompt": "' + "x" * 200000 + '"}', "[" * 5000 + "]" * 5000]
    for raw in payloads:
        for tail in (["cat"], ["head", "-c", "0"]):  # the second closes the pipe early
            p1 = subprocess.Popen([sys.executable, me], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
            p2 = subprocess.Popen(tail, stdin=p1.stdout, stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL)
            p1.stdout.close()
            p1.stdin.write(raw.encode())
            p1.stdin.close()
            p2.wait()
            rc = p1.wait()
            if rc != 0:
                bad += 1
                print("FAIL exit=%d for %r into %s" % (rc, raw[:40], tail[0]))

    print("%d classification cases, %d exit-code runs, %d failed"
          % (len(_CASES), len(payloads) * 2, bad))
    if bad:
        sys.exit(1)
    print("SHAPER OK")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        _selftest()
    else:
        main()
