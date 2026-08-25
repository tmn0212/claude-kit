#!/usr/bin/env python3
"""Stop hook: do not end a turn on a performance claim with nothing behind it.

The rule this enforces is one people already hold and forget under load: a
number that changes a decision has to say how it is known. `measured`,
`derived`, `from a spec`, `assumed`, `not verified`. The words cost nothing and
their absence is invisible, which is exactly the shape a hook is good at.

WHAT IT FIRES ON, and the narrowness is the point:

  a number with a unit or an "Nx",
  within a sentence that also makes a COMPARISON,
  outside any code fence,
  in a message that carries no evidence label anywhere.

All four have to hold. "The file is 200 lines" has no comparison. "21.9 MB/s,
`measured (n=3)`" has a label. A benchmark table pasted inside a fence is
output, not a claim. That leaves roughly one shape: an unlabelled assertion that
something is faster, slower, or costs N of something.

WHAT IT DOES NOT DO. It cannot tell whether the label is TRUE. Nothing can, from
text alone. `claim record` and `claim verify` are the half that checks, by
binding the number to the code that produced it. This hook only makes the
omission visible at the moment it happens.

HARD RULES:

1. **Once per session.** A Stop hook that fires repeatedly is a hook that gets
   disabled. The harness also gives up after 8 consecutive blocks, so a loop
   here would burn that budget and then let the claim through anyway.
2. **Fail open.** Any error and the turn ends normally.
3. **Never block twice for the same reason.** The marker is written before the
   block, not after.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "lib"))

try:
    from kit_config import Config
except Exception:  # pragma: no cover
    Config = None

DEFAULT_LABELS = [
    "measured",
    "derived",
    "from a spec",
    "from the datasheet",
    "read in the source",
    "from docs",
    "assumed",
    "unverified",
    "not verified",
    "not reproduced",
    "estimated",
]

# A number carrying a unit, or a bare multiplier.
_NUMBER = re.compile(
    r"\b\d+(?:[.,]\d+)?\s*"
    r"(?:x\b|%|MB/s|GB/s|KB/s|kB/s|Mbps|Gbps|ms\b|µs\b|us\b|ns\b|s\b|fps\b|Hz\b|kHz\b|MHz\b|"
    r"IOPS\b|MB\b|GB\b|KB\b|tokens?/s)",
    re.I,
)
# Words that turn a number into a claim about something being better or worse.
_COMPARISON = re.compile(
    r"\b(faster|slower|quicker|speedup|speed-up|improv\w*|regress\w*|reduc\w*|"
    r"increas\w*|decreas\w*|overhead|cheaper|costlier|throughput|latency|"
    r"bandwidth|beats?|outperform\w*|worse|better|gain\w*|penalt\w*|"
    r"ceiling|bottleneck)\b",
    re.I,
)
_FENCE = re.compile(r"```.*?```", re.S)
_INLINE_CODE = re.compile(r"`[^`\n]*`")


def payload() -> dict:
    try:
        raw = sys.stdin.read()
    except Exception:
        return {}
    try:
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def last_assistant_text(transcript: Path) -> str:
    """The text of the final assistant message, or an empty string.

    Reads a bounded tail: these transcripts reach many megabytes and this runs
    at the end of every turn.
    """
    try:
        with open(transcript, "rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - 300_000))
            tail = handle.read().decode("utf-8", errors="replace")
    except OSError:
        return ""
    text = ""
    for line in tail.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except Exception:
            continue
        if record.get("type") != "assistant" or record.get("isSidechain"):
            continue
        blocks = (record.get("message") or {}).get("content") or []
        parts = [b.get("text", "") for b in blocks if isinstance(b, dict) and b.get("type") == "text"]
        if parts:
            text = "\n".join(parts)
    return text


def unlabelled_claim(text: str, labels: list[str]) -> str | None:
    """The first sentence that claims a comparison in numbers with no label.

    Code is stripped first. A number inside a fence is output that was produced,
    which is the opposite of an unsupported claim, and stripping inline code
    keeps a bare `21.9` in a path or a flag from counting.
    """
    prose = _FENCE.sub(" ", text)
    lowered = prose.lower()
    if any(label in lowered for label in labels):
        return None
    prose = _INLINE_CODE.sub(" ", prose)
    for sentence in re.split(r"(?<=[.!?])\s+|\n\n", prose):
        if len(sentence) > 400:
            continue
        if _NUMBER.search(sentence) and _COMPARISON.search(sentence):
            return " ".join(sentence.split())[:200]
    return None


def main() -> int:
    data = payload()
    cfg = None
    if Config is not None:
        try:
            cfg = Config.load(os.environ.get("CLAUDE_PROJECT_DIR"))
        except Exception:
            cfg = None
    if cfg is None:
        return 0

    def setting(key, default):
        try:
            value = cfg.get(key, default)
            return default if value is None else value
        except Exception:
            return default

    if not setting("claims.gate", True):
        return 0

    # A Stop hook that already blocked this turn must not block again. The
    # harness gives up after 8 consecutive blocks; spending that budget here
    # would let the claim through anyway, having wasted eight turns.
    if data.get("stop_hook_active"):
        return 0

    transcript = data.get("transcript_path")
    session = re.sub(r"[^A-Za-z0-9_-]", "", str(data.get("session_id") or ""))
    if not transcript or not session or not Path(transcript).is_file():
        return 0

    marker = cfg.root / "log" / "claimgate" / session
    if marker.exists():
        return 0

    labels = [str(x).lower() for x in setting("claims.labels", DEFAULT_LABELS)]
    found = unlabelled_claim(last_assistant_text(Path(transcript)), labels)
    if not found:
        return 0

    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("1")
    except OSError:
        return 0

    sys.stdout.write(
        json.dumps(
            {
                "decision": "block",
                "reason": (
                    "This answer makes a comparative claim in numbers and never says "
                    "how the number is known:\n\n"
                    f"    {found}\n\n"
                    "Add one label next to it: "
                    + ", ".join(labels[:6])
                    + ".\n\n"
                    "`measured` means you ran it and are quoting the output; say the "
                    "sample size when it matters. If you did not check, say `not "
                    "verified` and say what would check it. Plausibility is not a "
                    "result.\n\n"
                    "If the number is worth keeping, bind it to the code that "
                    "produced it:\n"
                    "    claim record --id <name> --value <n> --unit <u> \\\n"
                    "                 --cmd '<how>' --source <file> --cond '<conditions>'\n\n"
                    "This fires once per session."
                ),
            }
        )
    )
    return 0


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    try:
        sys.stdout.flush()
    except Exception:
        pass
    os._exit(0)
