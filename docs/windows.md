# Windows

Everything in claude-kit is Python, and every hook uses exec form, which spawns
the interpreter directly with no shell involved. That removes most of what
usually breaks a Claude Code plugin on Windows. What remains is one setting and
a few things worth knowing.

## The one setting

**Set the `python` option to `py` on every plugin that has one.**

Four of the five do. `writing` ships no hooks, so it declares no options and
passing `--config` to it earns a warning rather than doing anything.

Windows ships `python.exe` and `py.exe`. Linux and macOS usually ship `python3`
and often no bare `python` at all. Measured on the machine this kit was built
on: `python3` present, `python` absent.

There is no way for one static manifest to name an interpreter that resolves on
both. Claude Code's hook `if` field filters on tool patterns, not on the
operating system, and every matching handler runs, so shipping a `python3` entry
and a `py` entry would run both wherever both exist.

The mechanism that does work is `userConfig`. Each plugin declares a `python`
option, defaulting to `python3`, and Claude Code substitutes it into the hook's
`command`. Set it once per plugin, either at install time:

```
claude plugin install session-economics@claude-kit --scope user --config python=py
```

or later, from inside a session, with `/plugin configure session-economics@claude-kit`.
There is no `claude plugin configure` subcommand.

## Why `.gitattributes` is in the repo

A `git clone` on Windows with the installer's default `core.autocrlf=true`
rewrites every line ending. That turns `#!/usr/bin/env python3` into
`#!/usr/bin/env python3\r`, and the script dies with a bad-interpreter error.

Three closed upstream Claude Code issues trace Windows plugin breakage to
exactly this. The `.gitattributes` at the repo root pins `*.py` and `*.sh` to
LF and `*.ps1` and `*.cmd` to CRLF, which settles it before anyone clones.

If you fork this repo, keep that file.

## The `bin/` directory

A plugin's `bin/` joins the Bash tool's PATH while the plugin is enabled. On
POSIX a shebang script is directly executable, so `kb` works as-is. Windows PATH
resolution needs a real executable, so all eight tools also ship a `.cmd` twin.

Those twins pick their own interpreter: `py` when `where py` finds it, else
`python`. They do NOT read the plugin's `python` option, because `userConfig`
substitution reaches `hooks.json` and nothing else.

## What does not run natively

Nothing in claude-kit needs a POSIX shell. `install.sh` is a bash entry point,
and `install.ps1` is the PowerShell twin; both do nothing except find an
interpreter and run `install.py`, so the logic exists once.

## Known upstream issue

There is an open Claude Code report of hook processes on Windows being left
suspended and never executing, which hangs the turn. It predates this kit and
nothing here can work around it. If a hook appears to hang, disable the plugin's
hooks with `guards.enabled = false` in `claude-kit.toml` and report it upstream.

## What has not been verified

The kit was built and tested on Linux. Every Windows claim above comes from the
Claude Code documentation and from upstream issue reports, not from a run on a
Windows machine. The `.cmd` launchers, the `py` substitution and the PowerShell
installer are `unverified` in the strict sense: they follow the documented
contract but nobody has executed them on Windows yet.

If you run it there, the useful check is `python3 tests/smoke.py`, which drives
every tool and asserts on each success signal.
