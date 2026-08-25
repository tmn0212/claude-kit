---
id: 0006
status: accepted
date: 2026-08-25
title: Keep the prose virtualenv outside the versioned plugin directory
supersedes:
superseded_by:
applies_to:
---

# ADR 0006 - Keep the prose virtualenv outside the versioned plugin directory

## Context

The `prose` engine needs `textstat` and `plotext`, which cannot be assumed
present in whatever interpreter Claude Code happens to run with. So it needs a
virtualenv, and the question is where.

An installed plugin lives at
`~/.claude/plugins/cache/claude-kit/writing/<version>/`. The version is in the
path.

## Options

### A. Beside the plugin

The obvious place, and wrong: `claude plugin update` writes a new version
directory, so the venv has to be rebuilt on every release. This was the original
implementation and the flaw only surfaced when the plugin was installed from the
marketplace rather than run from a checkout.

### B. In the user cache

`$XDG_CACHE_HOME/claude-kit/prose`, outside the version tree. One venv shared by
every version, and the requirements almost never change.

## Decision

B. The user cache is tried first; the plugin-local `.venv` remains a second
choice so an existing one in a development checkout is still found rather than
ignored.

## Consequences

`prose --setup` creates it once and it survives every update.

A wrapper at `~/bin/prose` globs for the highest installed version, because
inside a session the plugin's `bin/` is already on the Bash tool's PATH but in
an ordinary terminal it is not. That wrapper replaced a symlink to a separate
copy of the engine which was four fixes behind, including a path regex that
finds no transcripts on Windows.

The same reasoning applies to anything else the kit writes: nothing that must
outlive an update may live beside the plugin. `${CLAUDE_PLUGIN_DATA}` is the
supported place for a plugin's own persistent state.
