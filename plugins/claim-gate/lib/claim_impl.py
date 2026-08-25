"""A ledger that binds a measured number to the thing that produced it.

The gap this fills, from a survey of what exists: plenty of tools check an
agent's code against code, and none check a claim against a measurement.
`hyperfine` and Criterion compute a sample size and a spread, Bencher stores
them, and nothing consumes them. There is no published gate that refuses the
word "faster" without an n and a condition string.

So this is small and deliberately unclever. A claim is a number, its unit, the
command that produced it, the source files that command depends on, and the
conditions it was taken under. Recording one hashes those sources. Verifying
re-hashes them and reports what moved.

    claim record --id sd.aligned --value 21.9 --unit MB/s \\
                 --cmd "./bench.sh 2.1b" --source src/sd.c --cond "n=3, A2 card"
    claim list
    claim show sd.aligned
    claim verify              every claim, and whether its evidence still holds
    claim verify --id sd.aligned

`CLAIM OK` on stdout is the success signal. `claim verify` prints
`CLAIM DRIFT` and returns 1 when a claim's evidence has moved, which is what
makes it usable as a CI gate.

The ledger is `claims/ledger.jsonl` at the project root and belongs in git. It
is evidence, not state: a number without the conditions it was taken under is
noise, and a number whose producing code has since changed is worse than noise
because it still looks like an answer.
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import subprocess
import sys
from pathlib import Path

from kit_config import Config, rel

LEDGER = Path("claims") / "ledger.jsonl"


def ledger_path(cfg: Config) -> Path:
    return cfg.root / str(cfg.get("claims.ledger", str(LEDGER)))


def digest(path: Path) -> str | None:
    """A content hash, or None when the file is gone.

    Content rather than mtime: a checkout, a rebase or a `touch` all move mtime
    without changing what the measurement depended on, and a staleness signal
    that cries wolf is one people turn off.
    """
    try:
        return hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    except OSError:
        return None


def git_head(root: Path) -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def load(cfg: Config) -> list[dict]:
    path = ledger_path(cfg)
    if not path.is_file():
        return []
    records = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            # A torn line from an interrupted write. Skip it rather than die:
            # one bad record must not make the whole ledger unreadable.
            #
            # And recover what follows it. An interrupted write leaves no
            # trailing newline, so the NEXT record was appended onto the same
            # line: skipping the whole line lost two records, not one.
            tail = line.rfind('{"')
            if tail > 0:
                try:
                    records.append(json.loads(line[tail:]))
                except json.JSONDecodeError:
                    pass
            continue
    return records


def latest(records: list[dict]) -> dict[str, dict]:
    """The newest record per id. The ledger is append-only, so a re-measurement
    is a new line rather than an edit, and the history stays readable."""
    out: dict[str, dict] = {}
    for record in records:
        ident = record.get("id")
        if not ident:
            continue
        if ident not in out or record.get("recorded", "") >= out[ident].get("recorded", ""):
            out[ident] = record
    return out


def cmd_record(cfg: Config, args) -> int:
    root = cfg.root
    # A measurement is a number. Recording "about twice as fast" as a value
    # gives a ledger that cannot be compared, plotted, or checked against a
    # threshold, while still looking like data.
    try:
        float(str(args.value).replace(",", ""))
    except ValueError:
        sys.stderr.write(
            f"claim: --value must be a number, got {args.value!r}\n"
            "  Put the words in --cond, which is where the conditions belong.\n"
        )
        return 1
    sources = {}
    missing = []
    outside = []
    for name in args.source or []:
        path = (root / name).resolve()
        # Inside the repo, or the ledger stops being portable. `rel()` falls back
        # to an absolute path when it cannot make one relative, and the ledger is
        # committed, so an outside source makes `claim verify` fail for everyone
        # but the machine that recorded it.
        try:
            path.relative_to(root.resolve())
        except ValueError:
            outside.append(name)
            continue
        got = digest(path)
        if got is None:
            missing.append(name)
        else:
            sources[rel(path, root)] = got
    if outside:
        sys.stderr.write(
            f"claim: source outside the project: {', '.join(outside)}\n"
            "  The ledger is committed, so an absolute path makes `claim verify`\n"
            "  fail on every other checkout. Name a file inside the repo.\n"
        )
        return 1
    if missing:
        sys.stderr.write(f"claim: no such source file: {', '.join(missing)}\n")
        return 1
    if not sources:
        sys.stderr.write(
            "claim: at least one --source is required.\n"
            "  A number with nothing behind it cannot go stale, which means it\n"
            "  also cannot be checked. Name the file that produced it.\n"
        )
        return 1

    record = {
        "id": args.id,
        "value": args.value,
        "unit": args.unit,
        "cond": args.cond or "",
        "cmd": args.cmd or "",
        "sources": sources,
        "commit": git_head(root),
        "recorded": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "label": args.label,
    }
    path = ledger_path(cfg)
    path.parent.mkdir(parents=True, exist_ok=True)
    # If a previous write was interrupted the file has no trailing newline, and
    # appending would continue that broken line - losing this record as well as
    # the torn one. Close the line first.
    if path.exists() and path.stat().st_size and not path.read_bytes().endswith(b"\n"):
        with open(path, "a", encoding="utf-8") as handle:
            handle.write("\n")
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(record) + "\n")
    print(f"recorded {args.id} = {args.value} {args.unit}  [{args.label}]")
    print(f"  bound to {len(sources)} source file(s) at {record['commit'] or 'no commit'}")
    print("CLAIM OK")
    return 0


def cmd_list(cfg: Config, args) -> int:
    records = latest(load(cfg))
    if not records:
        print("no claims recorded yet")
        print("CLAIM OK")
        return 0
    print(f"{'ID':<28} {'VALUE':>12} {'UNIT':<10} {'LABEL':<10} RECORDED")
    for ident in sorted(records):
        record = records[ident]
        print(
            f"{ident:<28} {record.get('value', ''):>12} {str(record.get('unit', '')):<10} "
            f"{str(record.get('label', '')):<10} {record.get('recorded', '')[:10]}"
        )
    print(f"\n{len(records)} claim(s)")
    print("CLAIM OK")
    return 0


def cmd_show(cfg: Config, args) -> int:
    records = latest(load(cfg))
    record = records.get(args.id)
    if record is None:
        sys.stderr.write(f"claim: no claim with id '{args.id}'\n")
        return 1
    print(f"{record['id']} = {record.get('value')} {record.get('unit', '')}")
    print(f"  label      {record.get('label', '')}")
    print(f"  conditions {record.get('cond') or '(none recorded)'}")
    print(f"  command    {record.get('cmd') or '(none recorded)'}")
    print(f"  recorded   {record.get('recorded', '')} at {record.get('commit') or 'no commit'}")
    print("  sources")
    for name, want in (record.get("sources") or {}).items():
        got = digest(cfg.root / name)
        state = "gone" if got is None else ("ok" if got == want else "CHANGED")
        print(f"    {state:<8} {name}")
    history = [r for r in load(cfg) if r.get("id") == args.id]
    if len(history) > 1:
        print(f"  {len(history)} recordings, oldest {history[0].get('recorded', '')[:10]}")
    print("CLAIM OK")
    return 0


def cmd_verify(cfg: Config, args) -> int:
    records = latest(load(cfg))
    if args.id:
        records = {k: v for k, v in records.items() if k == args.id}
        if not records:
            sys.stderr.write(f"claim: no claim with id '{args.id}'\n")
            return 1
    if not records:
        print("no claims recorded yet")
        print("CLAIM OK")
        return 0

    drifted = []
    for ident in sorted(records):
        record = records[ident]
        problems = []
        for name, want in (record.get("sources") or {}).items():
            got = digest(cfg.root / name)
            if got is None:
                problems.append(f"gone: {name}")
            elif got != want:
                problems.append(f"changed: {name}")
        if problems:
            drifted.append((ident, record, problems))

    for ident, record, problems in drifted:
        print(f"\nDRIFT  {ident} = {record.get('value')} {record.get('unit', '')}")
        print(f"       recorded {record.get('recorded', '')[:10]}"
              f" at {record.get('commit') or 'no commit'}")
        for problem in problems:
            print(f"       {problem}")
        if record.get("cmd"):
            print(f"       re-measure: {record['cmd']}")

    checked = len(records)
    if drifted:
        print(f"\n{len(drifted)} of {checked} claim(s) rest on evidence that has moved.")
        print("A number whose producing code has changed still looks like an answer.")
        print("Re-measure, or record a new value: the ledger is append-only.")
        print("CLAIM DRIFT")
        return 1
    print(f"{checked} claim(s), every source unchanged")
    print("CLAIM OK")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="claim",
        description="Bind a measured number to the code that produced it, and notice when that moves.",
    )
    sub = parser.add_subparsers(dest="command")

    record = sub.add_parser("record", help="add a measurement to the ledger")
    record.add_argument("--id", required=True, help="stable identifier, e.g. sd.aligned-read")
    record.add_argument("--value", required=True, help="the number")
    record.add_argument("--unit", default="", help="MB/s, ms, fps, %%")
    record.add_argument("--cond", default="", help="the conditions it was taken under")
    record.add_argument("--cmd", default="", help="the command that produced it")
    record.add_argument(
        "--source",
        action="append",
        help="a file the measurement depends on; repeatable, at least one required",
    )
    record.add_argument(
        "--label",
        default="measured",
        choices=["measured", "derived", "spec", "assumed", "unverified"],
        help="how the number is known",
    )

    sub.add_parser("list", help="one line per claim")
    show = sub.add_parser("show", help="everything about one claim")
    show.add_argument("id")
    verify = sub.add_parser("verify", help="has any claim's evidence moved?")
    verify.add_argument("--id", help="check just this one")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.command:
        parser.print_help()
        return 2
    cfg = Config.load()
    return {
        "record": cmd_record,
        "list": cmd_list,
        "show": cmd_show,
        "verify": cmd_verify,
    }[args.command](cfg, args)
