---
id: 0001
status: accepted
date: 2026-08-25
title: Distribute as a plugin marketplace, not a dotfiles installer
supersedes:
superseded_by:
applies_to:
---

# ADR 0001 - Distribute as a plugin marketplace, not a dotfiles installer

## Context

The kit had to reach a new machine with as little effort as possible, and it
carries five kinds of thing: subagents, skills, an output style, hooks, and
command-line tools.

Three mechanisms were surveyed.

- A plugin marketplace. One git repo holds `.claude-plugin/marketplace.json`
  and the plugins, and two commands install it. A plugin ships skills,
  agents, output styles, hooks, MCP servers, and a `bin/` directory that
  joins the Bash tool's PATH while enabled.
- An installer script, the shape already used by `claude-statusline-grid`:
  clone, run `install.sh`, copy into `~/.claude`, merge `settings.json`.
- Symlinking `~/.claude` into a versioned repo.

## Options

### A. Plugin marketplace, with a small installer for the one gap

A plugin explicitly cannot ship a `CLAUDE.md`. Everything else it can.

### B. Installer script only

Works offline, no plugin cache semantics, but the merge logic becomes ours to
maintain and Windows needs a second implementation.

### C. Symlink farm

Rejected on evidence rather than taste. Four upstream issues were open at
the time.

- Claude refuses to write through a symlinked `CLAUDE.md`, as an
  intentional safety behaviour.
- Symlinks in `.claude/rules/` pointing outside the project are not
  loaded, which contradicts the documentation.
- `/skills` does not detect skills in symlinked directories.
- Marketplace `installLocation` is validated by string prefix, not realpath.

## Decision

A, the plugin marketplace, with `install.py` covering only the user-level
`CLAUDE.md`.

## Consequences

Installation is `claude plugin marketplace add tmn0212/claude-kit` plus one
`install` per plugin, on every platform, with no shell script involved.

Three constraints come with it, and all three are in `CONTRIBUTING.md`:

- Version pinning is by string. Pushing commits without bumping `version` in
  `plugin.json` leaves existing users on the cached copy, silently.
- A plugin runs from `~/.claude/plugins/cache/<marketplace>/<plugin>/<version>/`,
  so a `../` reference out of the plugin directory breaks, and anything written
  beside the plugin is destroyed by the next update. See ADR 0006.
- Relative `source` paths in `marketplace.json` do not resolve when somebody
  adds the marketplace by a direct URL to the JSON, so `owner/repo` is the
  documented form.
