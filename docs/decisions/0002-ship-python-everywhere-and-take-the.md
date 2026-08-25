---
id: 0002
status: accepted
date: 2026-08-25
title: Ship Python everywhere and take the interpreter name from userConfig
supersedes:
superseded_by:
applies_to:
---

# ADR 0002 - Ship Python everywhere and take the interpreter name from userConfig

## Context

The kit had to run on Linux, macOS and Windows. A portability audit of the
tooling it was extracted from found 34 bash files, 4,734 lines, and ranked the
Windows blockers: `jq` in every hook, GNU `date +%s%3N`, GNU `stat -c`, and
`/sys` paths for serial ports.

For the subset the kit actually needs, the bash surface was much smaller: about
950 lines across `adr.sh`, `promote.sh`, `brief.sh` and six hooks. Everything
else that failed on Windows was domain-specific and out of scope anyway.

Then a second problem, measured rather than assumed: this Linux machine has
`python3` and no `python`; Windows ships `python.exe` and `py.exe` and usually
no `python3`. A static hook manifest cannot name one interpreter that resolves
on both.

## Options

### A. Rewrite the subset in Python, exec-form hooks

Exec form (`command` plus `args`) spawns the interpreter directly with no shell,
which removes the `jq`, GNU-coreutils and CRLF problems at once.

### B. Require Git Bash

Cheapest. But one upstream issue was open reporting hook processes on Windows
left suspended and never executing, and three closed ones traced plugin
breakage to CRLF arriving from `git clone`.

### C. POSIX first, Windows later

## Decision

A. Everything ships as Python, hooks use exec form, and the interpreter name
comes from a `userConfig` option named `python`, defaulting to `python3`.

The `userConfig` route is the only mechanism that works. The hook `if` field
filters on tool patterns, not on the operating system, and every matching
handler runs, so shipping a `python3` entry and a `py` entry would execute both
wherever both exist.

## Consequences

Windows users set one option per plugin that HAS one, at install time with
`--config python=py` or afterwards with `/plugin configure`. There is no
`claude plugin configure` subcommand; that was checked against the CLI.

Four of the five plugins declare the option. `writing` ships no hooks, so it
declares no `userConfig` at all and passing `--config` to it earns a warning.
That is the contract working as designed: `userConfig` substitution reaches
`hooks.json` and nothing else.

`.gitattributes` pins `*.py` and `*.sh` to LF and `*.ps1` and `*.cmd` to CRLF,
because a Windows clone with the installer default rewrites every shebang into
an unrunnable `...python3\r`.

Eight `.cmd` launchers exist so the tools resolve on the Windows PATH, which
needs a real executable rather than a shebang. They pick `py` over `python`
themselves, because `userConfig` substitution reaches `hooks.json` and nothing
else.

**None of this is verified on Windows.** It was built and tested on Linux, and
`docs/windows.md` says so in as many words. `tests/smoke.py` is the check to run
there.
