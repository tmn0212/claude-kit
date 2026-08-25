---
id: 0007
status: accepted
date: 2026-08-25
title: "A claim gate: block an unlabelled number, and bind a measured one to its source"
supersedes:
superseded_by:
applies_to:
---

# ADR 0007 - A claim gate: block an unlabelled number, and bind a measured one to its source

## Context

A survey of what exists found three ecosystems and no bridge between them.

- Execution gates in the harness are mature. Anthropic's own guidance says to
  "have Claude show evidence rather than asserting success", and names four gate
  strengths, the strongest being a Stop hook.
- Existence checkers, for hallucinated APIs and packages, are research
  prototypes plus two CLIs at three or four stars.
- Calibrated confidence vocabularies are mature, but come from climate science
  and intelligence analysis. ICD 203 requires a source and a confidence level,
  and forbids mixing a confidence level with a probability in one sentence.

The gap, stated plainly: **nothing ties a claim to a measurement.** `hyperfine`
and Criterion compute a sample size and a spread, Bencher stores them, and no
gate consumes them. There is no published tool that refuses the word "faster"
without an n and a condition string, and no confidence-label linter exists.

Two research findings shaped the design rather than merely motivating it.

Intrinsic self-correction without external feedback does not work and often
makes output worse. So a self-review turn is not verification; only an executed
command or an independent check is.

Models are measurably more likely to err when the context already contains their
own earlier errors, and that does not go away with scale. So an unsupported
number is not merely unhelpful, it is load-bearing for every later turn.

## Options

### A. A linter over documents

Check that every numeric claim in `docs/` carries a label. Catches claims that
land in a file, misses every claim that only ever appears in a reply.

### B. A Stop hook over the reply

Refuse to end a turn on an unlabelled comparative claim. Catches the reply,
which is where most claims live and die.

### C. Both, plus a ledger binding numbers to their source

## Decision

C, in two halves that are useful separately.

**The gate.** A Stop hook fires when four things hold at once: a number with a
unit, a comparison in the same sentence, both outside any code fence, and no
label anywhere in the message. Once per session.

**The ledger.** `claim record` binds a number to its unit, the command that
produced it, the conditions, and a content hash of every source file it depends
on. `claim verify` re-hashes and reports drift, exiting 1 so CI can gate on it.

## Consequences

The gate cannot tell whether a label is TRUE. Nothing can, from text alone. It
makes the omission visible at the moment it happens, and the ledger gives the
number somewhere to be checked. Claiming more than that would be theatre.

Narrowness is doing the work. The published prior art in this space matches on
claim STRINGS, which a rephrase defeats; requiring a number, a unit, a
comparison and the absence of a label is a shape rather than a phrase.

It fires once per session on purpose. The harness overrides a blocking Stop hook
after 8 consecutive blocks, so a loop would spend that budget and then let the
claim through anyway, having wasted eight turns.

Hashes are of content, not mtime. A checkout, a rebase or a `touch` all move
mtime without changing what the measurement depended on, and a staleness signal
that cries wolf is one people turn off.

`claim record` refuses a claim with no `--source`. A number with nothing behind
it cannot go stale, which means it also cannot be checked, and recording it
would give false assurance.

What is still not closed: nothing here verifies that a number came from the run
it says it did. That needs the measurement harness to emit a signed record, and
the honest position is that this is provenance for the DEPENDENCIES of a claim,
not for the claim itself. See ADR 0004 for the related convention that a tool's
success is a printed signal rather than an exit code.
