---
id: 0005
status: accepted
date: 2026-08-25
title: Hooks fail open, without exception
supersedes:
superseded_by:
applies_to:
---

# ADR 0005 - Hooks fail open, without exception

## Context

These hooks run before every tool call in every project where the plugin is
enabled. A hook that can crash is a hook that can wedge a project, and the
blast radius is every session on the machine, not one repo.

## Decision

Every hook exits 0 with empty stdout on any error: a missing file, an
unparsable payload, a broken import, a wrong-typed config value. No exceptions.

`guards.enabled = false` in `claude-kit.toml` turns off every hook the plugin
installs, not merely the refusing ones.

## Consequences

The dispatch is wrapped in a bare `except Exception`, which is normally a smell
and is correct here: the alternative is a traceback that blocks a tool call.

The exit itself is `os._exit(0)` after an explicit `sys.stdout.flush()`,
not `sys.exit(0)`. A deferred flush at interpreter shutdown can hit EPIPE,
and CPython rewrites the status to 120. The harness reads that as a hook
failure.

The `prompt_shaper.py` hook found this first and documented it with a
measurement. The six guards claimed to fail open while still using
`sys.exit`.

`tests/smoke.py` feeds every action a garbage payload and asserts exit 0 with an
empty stderr. That assertion alone is worthless: this hook always exits 0 by
design, so it passes against a table of `lambda: None`.

What makes it load-bearing is the wiring check beside it. Every action named in
a `hooks.json` must exist in the dispatch table, and every entry in the table
must be wired to an event. A review found `friction-fail` declared, wired, and
never executed by any test; the earlier version of this check grepped the hook's
own source text for the action name, which a dead handler also satisfies.

The cost of this rule is real: a guard that fails open lets through the thing it
exists to refuse. That trade is accepted. A missed refusal costs one polling
loop; a crashing hook costs the project.
