---
id: 0003
status: accepted
date: 2026-08-25
title: One TOML file at the project root is the whole configuration surface
supersedes:
superseded_by:
applies_to:
---

# ADR 0003 - One TOML file at the project root is the whole configuration surface

## Context

Every tool in the kit was extracted from one project and had that project's
directory names compiled into it. `kb.py` alone hardcoded seven source
directories and twenty-two lines of domain vocabulary; `bookcheck.py` hardcoded
a hardware confidence vocabulary; `promote.sh` hardcoded the document it
registers into.

## Options

### A. One config file per project

A `claude-kit.toml` at the project root names the source directories, the search
aliases, the decision directory and the guard thresholds.

### B. Generic defaults written into the scripts

Simpler, no config layer, but every project that wants a different layout forks
the file.

### C. Defaults plus a worked example

## Decision

A. `claude-kit.toml`, read through `kit_config.py`, with a default for every
key so a project with no file still works.

Discovery walks up from the working directory looking for the file, then for a
`.git`, then gives up and uses the working directory. That order lets a monorepo
put a config beside each sub-package.

## Consequences

`tomllib` is stdlib from Python 3.11, so this sets the version floor for the
whole kit and adds no dependency.

`CLAUDE_PROJECT_DIR` is deliberately NOT consulted during discovery. Letting it
override the working directory meant that running a tool inside project B during
a session rooted at project A quietly answered about A. The hooks pass it in
explicitly, which is the one place it is the right answer.

`kit_config.py` is duplicated across plugins on purpose. Plugins install as
separate cache copies and cannot import from each other.
