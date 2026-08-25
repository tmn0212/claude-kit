#!/usr/bin/env python3
"""prose - one entry point for measuring how readable an answer is.

Thresholds come from a measured baseline of 540 assistant messages across 25
sessions (2026-08-22), not from taste. See BASELINE.md.

    prose score  <file|->      score one piece of text
                  --doc        it is a document, not a reply: no word or bold cap
    prose lint   <file|->      vale only
    prose check  <file|->      score + lint + diagram check, one report
    prose base   [dir]         re-derive the corpus baseline from transcripts
    prose recent [N] [dir]     score the last N assistant messages
    prose chart  <file|->      label<TAB>value lines -> ascii bar chart
    prose diagram <file|->     validate a unicode box diagram
    prose ab     [A] [B]       A/B two output styles on the eval questions

check exits 1 if any hard limit is broken, so it can gate a hook or CI.
"""
import glob
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
# Rules, thresholds and the eval corpus live beside the engine rather than
# inside it, so a project can point PROSE_DATA at its own copy and still
# take engine updates.
DATA = os.environ.get("PROSE_DATA") or os.path.join(os.path.dirname(HERE), "vale")
VALE_INI = os.path.join(DATA, "vale.ini")

# Hard limits. A number here means the baseline measured it and it needs a bound.
LIMITS = {
    "words": 700,        # p90 was 741, nothing bounded it
    "fk": 12.0,          # p90 was 9.2, so this is headroom not a squeeze
    "longest_sentence": 40,
    "bold": 12,          # median 6, p90 18, worst 215
    "dense_bullets": 0,  # a bullet carrying 3+ clauses
    "diagram_width": 78,
}

FILLER = re.compile(
    r"\b(it'?s worth noting|essentially|fundamentally|in essence|"
    r"it'?s important to (?:understand|note|remember)|needless to say|"
    r"as you can see|that said|in other words|simply put|at the end of the day)\b",
    re.I,
)
PREAMBLE = re.compile(
    r"^\s*(?:great question|sure[,!.]|certainly|absolutely|of course|"
    r"i'?ll |let me |happy to |you'?re (?:right|absolutely right)|"
    r"here'?s |here is |based on )",
    re.I,
)
LATEX = re.compile(r"\\\(|\\\[|\$\$|\\frac|\\sum|\\int")
BOX = set("┌┐└┘├┤┬┴┼─│╭╮╰╯━┃╔╗╚╝║═▲▼◀▶←→↑↓")

# The house rule bans the em-dash, the en-dash and the middot outright: use a
# comma, a colon, a full stop, or start a new sentence. Vale says the same in
# styles/Minh/Emdash.yml, but Vale is optional and usually absent, so the rule
# has to be measurable here too or it is only advice.
TYPOGRAPHY = {"em/en dashes": "—–", "middots": "·"}


def without_code(t):
    """Drop fenced and inline code, keeping tables and ordinary prose.

    A dash inside a code sample is data, not writing, and a check that fails on
    a document *describing* the rule is one people switch off. Tables are kept
    deliberately: `strip_md` drops them for sentence metrics, but a middot in a
    table cell is still a middot in the prose.
    """
    t = re.sub(r"<!--\s*vale off\s*-->.*?<!--\s*vale on\s*-->", " ", t, flags=re.S | re.I)
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    return re.sub(r"`[^`]*`", " ", t)


def read(arg):
    if arg == "-":
        return sys.stdin.read()
    with open(arg, encoding="utf-8") as f:
        return f.read()


def strip_md(t):
    # Honour Vale's own off/on comments so the two tools agree on what counts.
    t = re.sub(r"<!--\s*vale off\s*-->.*?<!--\s*vale on\s*-->", " ", t, flags=re.S | re.I)
    t = re.sub(r"\A---\n.*?\n---\n", "", t, flags=re.S)          # yaml frontmatter
    t = re.sub(r"```.*?```", " ", t, flags=re.S)
    t = re.sub(r"^\s*>?\s*\|.*$", "", t, flags=re.M)             # tables, quoted or not
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    # A list item ends a sentence, the same way a heading does. Stripping the
    # marker and leaving the text bare ran a whole list together: six bullets
    # with no full stops measured as one 43-word sentence. The house rule is
    # that a bullet IS a sentence, so measuring it as one is the honest read,
    # and the separate no-stop check still reports the missing punctuation.
    # The whole item, including continuation lines, then one terminator. An
    # earlier version matched a single LINE, which put a full stop in the middle
    # of every wrapped bullet and understated the longest sentence - the exact
    # mirror of the heading bug this rule was added to fix.
    #
    # A bullet already ending in `?`, `!` or an ellipsis keeps it: appending
    # produced "Is it faster?." in reported output.
    def _item(match):
        body = " ".join(match.group(1).split())
        return body if body.endswith((".", "?", "!")) else body.rstrip(",;:") + "."

    t = re.sub(
        r"^[ \t]*[-*+][ \t]+(.+?)(?=\n[ \t]*(?:[-*+][ \t]|\n)|\Z)",
        _item, t, flags=re.M | re.S,
    )
    # A heading ENDS a sentence. Stripping the `#` and leaving the text bare
    # runs the heading into the paragraph below it, so "## Context" followed by
    # a 24-word opener measured as one 30-word sentence. Every document with
    # headings was affected, which is all of them, and the longest-sentence
    # number was wrong in the same direction every time.
    t = re.sub(r"^\s*#{1,6}\s+(.+?)\s*$", r"\1.", t, flags=re.M)
    t = re.sub(r"[*_#>]+", "", t)
    return t


def sentences(t):
    return [" ".join(s.split()) for s in re.split(r"(?<=[.!?])\s+", t)
            if len(s.split()) >= 3]


def bullets(raw):
    out = []
    for line in raw.splitlines():
        m = re.match(r"^\s*(?:[-*+]|\d+\.)\s+(.*)$", line)
        if m and len(m.group(1).split()) >= 4:
            out.append(m.group(1).strip())
    return out


def clause_count(b):
    """How many independent facts a bullet is carrying."""
    b = re.sub(r"`[^`]*`", "X", b)
    return 1 + len(re.findall(r"\s[-—–]\s|;|\s\(", b))


def as_doc():
    """Relax the answer-shaped limits: a rules file or a design doc is not a reply."""
    LIMITS["words"] = 100000
    LIMITS["bold"] = 100000


def count_diagrams(raw):
    """Count diagram BLOCKS, not box-drawing lines.

    Counting lines rewarded heavy borders: a five-box stack of mostly border
    scored 10 while a compact annotated pipeline using single `|` and `v`
    strokes scored 1, even though the second carries more information. A
    diagram is a fenced block with enough strokes spread over enough lines.
    """
    n = 0
    for block in re.findall(r"```[^\n]*\n(.*?)```", raw, flags=re.S):
        lines = [l for l in block.splitlines() if l.strip()]
        strokes = sum(c in BOX for c in block)
        if len(lines) >= 3 and strokes >= 4:
            n += 1
    return n


def score(raw):
    import textstat

    t = strip_md(raw)
    ss = sentences(t)
    lens = sorted(len(s.split()) for s in ss) or [0]
    bs = bullets(raw)
    dense = [b for b in bs if clause_count(b) >= 3]
    naked = [b for b in bs if not b.rstrip().endswith((".", ":", "?", "!"))
             and len(b.split()) > 14]
    body = raw.lstrip("#").lstrip()
    prose_only = without_code(raw)
    banned = {k: sum(prose_only.count(c) for c in chars) for k, chars in TYPOGRAPHY.items()}
    return {
        "dashes": banned["em/en dashes"],
        "middots": banned["middots"],
        "words": len(raw.split()),
        "sentences": len(ss),
        "fk": round(textstat.flesch_kincaid_grade(t), 1) if ss else 0.0,
        "ease": round(textstat.flesch_reading_ease(t), 1) if ss else 0.0,
        "median_sentence": lens[len(lens) // 2],
        "longest_sentence": lens[-1],
        "bold": raw.count("**") // 2,
        "bullets": len(bs),
        "dense_bullets": len(dense),
        "naked_bullets": len(naked),
        "tables": 1 if "\n|" in raw else 0,
        "diagrams": count_diagrams(raw),
        "mermaid": raw.count("```mermaid"),
        "latex": len(LATEX.findall(raw)),
        "filler": len(FILLER.findall(t)),
        "preamble": 1 if PREAMBLE.match(body) else 0,
        "_dense": dense[:5],
        "_naked": naked[:5],
    }


def report(s, name="text"):
    fails = []

    def row(label, key, limit=None, fmt="{:.0f}"):
        v = s[key]
        bad = limit is not None and v > limit
        if bad:
            fails.append(f"{label}: {fmt.format(v)} > {fmt.format(limit)}")
        mark = "FAIL" if bad else "ok"
        lim = f"<= {fmt.format(limit)}" if limit is not None else ""
        print(f"  {label:<22}{fmt.format(v):>8}   {lim:<8} {mark}")

    print(f"\n{name}")
    row("words", "words", LIMITS["words"])
    row("flesch-kincaid grade", "fk", LIMITS["fk"], "{:.1f}")
    row("reading ease", "ease", None, "{:.1f}")
    row("median sentence", "median_sentence")
    row("longest sentence", "longest_sentence", LIMITS["longest_sentence"])
    row("bold spans", "bold", LIMITS["bold"])
    row("bullets", "bullets")
    row("bullets w/ 3+ facts", "dense_bullets", LIMITS["dense_bullets"])
    row("long bullets, no stop", "naked_bullets")
    row("tables", "tables")
    row("diagrams", "diagrams")
    row("mermaid blocks", "mermaid", 0)
    row("latex spans", "latex", 0)
    row("filler phrases", "filler", 0)
    row("preamble opening", "preamble", 0)
    row("em/en dashes", "dashes", 0)
    row("middots", "middots", 0)
    for b in s["_dense"]:
        print(f"    dense bullet ({clause_count(b)} facts): {b[:90]}")
    for b in s["_naked"]:
        print(f"    unpunctuated bullet: {b[:90]}")
    return fails


def lint(path):
    vale = (shutil.which("vale") or os.path.join(DATA, "bin", "vale")
            if os.path.exists(os.path.join(DATA, "bin", "vale"))
            else shutil.which("vale") or os.path.expanduser("~/bin/vale"))
    if not os.path.exists(vale):
        print("  vale not installed, skipping")
        return []
    r = subprocess.run([vale, "--config", VALE_INI, "--output=line", path],
                       capture_output=True, text=True)
    out = [l for l in (r.stdout + r.stderr).splitlines() if l.strip()]
    for l in out[:25]:
        print("  " + l)
    # A suggestion informs; it does not fail a check. Rules that cannot be
    # decided by regex alone (Unverified) live at that level on purpose.
    soft = ("Minh.Unverified",)
    return [l for l in out if not any(s in l for s in soft)]


def check_diagram(raw):
    fails = []
    lines = [l.rstrip() for l in raw.splitlines()]
    box = [(i, l) for i, l in enumerate(lines, 1) if sum(c in BOX for c in l) >= 3]
    if not box:
        print("  no box-drawing lines found")
        return fails
    print(f"  {len(box)} box-drawing lines")
    for i, l in box:
        if len(l) > LIMITS["diagram_width"]:
            fails.append(f"line {i}: {len(l)} cols > {LIMITS['diagram_width']}")
            print(f"  FAIL line {i}: {len(l)} columns")
    verts = {}
    for i, l in box:
        for c, ch in enumerate(l):
            if ch in "│┃║":
                verts.setdefault(c, []).append(i)
    stray = [c for c, rows in verts.items() if len(rows) == 1]
    if stray:
        print(f"  note: {len(stray)} vertical stroke(s) appear on one line only "
              f"at column(s) {sorted(stray)[:8]} - check alignment")
    if not fails:
        print("  width ok")
    return fails


def chart(raw):
    import plotext as plt

    labels, vals = [], []
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        parts = re.split(r"\t|\s{2,}|,", line.strip())
        parts = [p for p in parts if p]
        if len(parts) < 2:
            continue
        try:
            vals.append(float(parts[-1]))
        except ValueError:
            continue
        labels.append(" ".join(parts[:-1]))
    if not labels:
        print("no 'label<TAB>value' lines found")
        return
    plt.clear_figure()
    plt.simple_bar(labels, vals, width=68)
    plt.show()


def corpus(d):
    for p in sorted(glob.glob(os.path.join(d, "*.jsonl"))):
        with open(p, encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("type") != "assistant" or r.get("isSidechain"):
                    continue
                for c in (r.get("message") or {}).get("content") or []:
                    if isinstance(c, dict) and c.get("type") == "text":
                        s = (c.get("text") or "").strip()
                        if len(s.split()) >= 40:
                            yield os.path.basename(p)[:8], s


def default_dir():
    # Claude Code flattens the project path, replacing every character that is
    # not a letter or a digit with '-'. Naming the separators individually is
    # what broke this on Windows: it left the drive colon in place, so the
    # directory looked for was `C:-Users-...` and the real one is `C--Users-...`.
    cwd = re.sub(r"[^A-Za-z0-9]", "-", os.getcwd())
    # Honour CLAUDE_CONFIG_DIR the way the installer does: a user who moved
    # their config directory has no transcripts under ~/.claude at all.
    base = os.environ.get("CLAUDE_CONFIG_DIR") or os.path.expanduser("~/.claude")
    return os.path.join(base, "projects", cwd)


def baseline(d):
    rows = [score(s) for _, s in corpus(d)]
    if not rows:
        print(f"no transcripts under {d}")
        return
    n = len(rows)

    def p(key, q):
        v = sorted(r[key] for r in rows)
        return v[min(int(len(v) * q), len(v) - 1)]

    print(f"corpus: {n} assistant messages of 40+ words\n")
    print(f"{'metric':<24}{'p10':>8}{'median':>9}{'p90':>8}{'max':>9}")
    print("-" * 58)
    for label, key in [("words", "words"), ("flesch-kincaid", "fk"),
                       ("reading ease", "ease"), ("median sentence", "median_sentence"),
                       ("longest sentence", "longest_sentence"), ("bold spans", "bold"),
                       ("bullets w/ 3+ facts", "dense_bullets")]:
        print(f"{label:<24}{p(key,.1):>8.1f}{p(key,.5):>9.1f}"
              f"{p(key,.9):>8.1f}{p(key,1.0):>9.1f}")
    print()
    for label, key in [("with a table", "tables"), ("with a diagram", "diagrams"),
                       ("with mermaid", "mermaid"), ("with filler", "filler"),
                       ("preamble opening", "preamble"),
                       ("with an em/en dash", "dashes"), ("with a middot", "middots")]:
        print(f"  {label:<22}{100*sum(1 for r in rows if r[key])/n:5.1f}%")


def recent(n, d):
    msgs = list(corpus(d))[-n:]
    for i, (src, s) in enumerate(msgs, 1):
        report(score(s), f"[{i}/{len(msgs)}] {src} ({len(s.split())} words)")


EVAL_DIR = os.path.join(DATA, "eval")


def questions():
    """Parse eval/questions.md into (slug, prompt) pairs."""
    path = os.path.join(EVAL_DIR, "questions.md")
    blocks = re.split(r"^## ", open(path, encoding="utf-8").read(), flags=re.M)[1:]
    out = []
    for b in blocks:
        head, _, body = b.partition("\n")
        body = body.strip()
        if body:
            out.append((re.sub(r"\W+", "-", head.strip().lower()), body))
    return out


def run_variant(style, slug, prompt, model, out_dir, timeout=180, tries=2):
    """One headless run. Cached: an existing answer file is reused.

    A short timeout with a retry beats a long one: a single hung run at 600s
    made a 2-minute sweep take 10 minutes.
    """
    dest = os.path.join(out_dir, re.sub(r"\W+", "-", style.lower()), slug + ".md")
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.exists(dest) and os.path.getsize(dest) > 200:
        return dest, True
    claude = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
    cmd = [claude, "-p", prompt, "--settings", json.dumps({"outputStyle": style}),
           "--allowedTools", "", "--model", model, "--output-format", "text"]
    last = ""
    for attempt in range(tries):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            last = f"timed out after {timeout}s"
            continue
        text = (r.stdout or "").strip()
        if text:
            with open(dest, "w", encoding="utf-8") as f:
                f.write(text + "\n")
            return dest, False
        last = r.stderr.strip()[:200] or "empty stdout"
    raise RuntimeError(f"{style}/{slug}: {last} (after {tries} tries)")


def ab(variants, model, out_dir, limit=None, workers=0):
    from concurrent.futures import ThreadPoolExecutor, as_completed

    qs = questions()[:limit]
    jobs = [(v, s, p) for v in variants for s, p in qs]
    # Each job is a subprocess doing network I/O, so the pool costs nothing to
    # widen. Default to running every job at once: wall clock becomes one run.
    workers = workers or max(1, len(jobs))
    print(f"{len(qs)} questions x {len(variants)} variants = {len(jobs)} runs, "
          f"model {model}, {workers} at a time\ncache: {out_dir}\n"
          f"a cached answer is reused, so a re-run is instant\n")
    results = {}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(run_variant, v, s, p, model, out_dir): (v, s)
                for v, s, p in jobs}
        # as_completed, not insertion order: otherwise the first slow run blocks
        # every later print and a parallel run looks serial.
        for n, f in enumerate(as_completed(futs), 1):
            v, s = futs[f]
            try:
                dest, cached = f.result()
                results[(v, s)] = score(open(dest, encoding="utf-8").read())
                print(f"  [{n}/{len(jobs)}] {'cached' if cached else 'ran   '}  {v} / {s}",
                      flush=True)
            except Exception as e:
                print(f"  [{n}/{len(jobs)}] FAILED  {v} / {s}: {e}", flush=True)

    keys = ["words", "fk", "ease", "median_sentence", "longest_sentence",
            "bold", "dense_bullets", "naked_bullets", "tables", "diagrams"]
    labels = {"words": "words", "fk": "flesch-kincaid", "ease": "reading ease",
              "median_sentence": "median sentence", "longest_sentence": "longest sentence",
              "bold": "bold spans", "dense_bullets": "bullets w/ 3+ facts",
              "naked_bullets": "long bullets, no stop", "tables": "has a table",
              "diagrams": "diagram lines"}

    a, b = variants[0], variants[1] if len(variants) > 1 else variants[0]
    # Only average questions every variant answered. Averaging old over 6 and
    # new over 5 compares different question sets and reads as a real delta.
    paired = [s for s, _ in qs if all((v, s) in results for v in variants)]
    dropped = [s for s, _ in qs if s not in paired]
    if dropped:
        print(f"\ndropped, not answered by every variant: {', '.join(dropped)}")

    def mean(v, k):
        vals = [results[(v, s)][k] for s in paired]
        return sum(vals) / len(vals) if vals else float("nan")

    print(f"\nmean over {len(paired)} paired questions\n")
    w = max(len(a), len(b), 12)
    print(f"{'metric':<24}{a[:w]:>{w+2}}{b[:w]:>{w+2}}{'delta':>10}")
    print("-" * (24 + 2 * (w + 2) + 10))
    for k in keys:
        va, vb = mean(a, k), mean(b, k)
        print(f"{labels[k]:<24}{va:>{w+2}.1f}{vb:>{w+2}.1f}{vb-va:>+10.1f}")
    print(f"\nanswers are in {out_dir}, diff them to see what changed")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return 0
    cmd, rest = sys.argv[1], sys.argv[2:]
    if "--doc" in rest:
        rest.remove("--doc")
        as_doc()
    if cmd == "score":
        return 1 if report(score(read(rest[0])), rest[0]) else 0
    if cmd == "lint":
        return 1 if lint(rest[0]) else 0
    if cmd == "check":
        raw = read(rest[0])
        f = report(score(raw), rest[0])
        print("\nvale")
        f += lint(rest[0])
        print("\ndiagram")
        f += check_diagram(raw)
        print("\n" + ("PROSE OK" if not f else f"PROSE FAIL ({len(f)})"))
        for x in f[:15]:
            print("  " + str(x))
        return 1 if f else 0
    if cmd == "base":
        baseline(rest[0] if rest else default_dir())
        return 0
    if cmd == "recent":
        n = int(rest[0]) if rest and rest[0].isdigit() else 5
        recent(n, rest[1] if len(rest) > 1 else default_dir())
        return 0
    if cmd == "chart":
        chart(read(rest[0]))
        return 0
    if cmd == "diagram":
        return 1 if check_diagram(read(rest[0])) else 0
    if cmd == "ab":
        opts = {"--model": "sonnet", "--limit": None, "--workers": "6"}
        for k in list(opts):
            if k in rest:
                i = rest.index(k)
                opts[k] = rest[i + 1]
                del rest[i:i + 2]
        vs = rest or ["Answer first old", "Answer first"]
        ab(vs, opts["--model"],
           os.path.join(EVAL_DIR, "answers"),
           int(opts["--limit"]) if opts["--limit"] else None,
           int(opts["--workers"]))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
