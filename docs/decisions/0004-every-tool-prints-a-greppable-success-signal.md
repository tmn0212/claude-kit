---
id: 0004
status: accepted
date: 2026-08-25
title: Every tool prints a greppable success signal on stdout
supersedes:
superseded_by:
applies_to:
---

# ADR 0004 - Every tool prints a greppable success signal on stdout

## Context

An agent driving a command-line tool has one signal to check, and exit code 0 is
not enough: a tool that half-worked, printed a partial answer and exited 0 is
indistinguishable from one that worked.

This is not hypothetical. `friction.py --brief` in the source project crashed
after printing its first line, and because the session-start hook grepped for
`BRIEF OK` rather than trusting the exit code, the failure showed up as a
truncated brief rather than as a silent wrong answer.

## Decision

Every tool prints a greppable success line on stdout as its last act: `KB OK`,
`ADR OK`, `PROMOTE OK`, `TOKENCOST OK`, `FRICTION OK`, `BRIEF OK`, `PROSE OK`,
`SMOKE OK`.

**Grep for the line, not exit code 0.** Anything driving these greps.

## Consequences

The convention has to be kept in anything new, and `CONTRIBUTING.md` says so.

`tests/smoke.py` asserts on the signal rather than the return code for every
tool it drives, which is what made three separate half-working states visible
during the build rather than after it.

A tool that exits non-zero for an EMPTY result rather than a failure breaks the
convention from the other end. `tokencost` did this: it returned 1 when the
transcript directory did not exist, which is the state of every project on its
first day. That now prints the signal and returns 0.
