# The lenses

Six angles. Pick the ones the ask actually has, never all six by default. Two
lenses that would read the same files are one lens.

## 1. What this codebase already does

The strongest lens, and the one people skip. Most asks are a variant of something
the tree already solves, and the existing pattern beats a better idea that sits
alone.

Brief it with: find the nearest existing implementation, name its files and
entry points, and say how the new thing would either extend it or sit beside it.

Agent: a project-specific search agent if one exists, otherwise `Explore`.

## 2. What has already been decided

Design records, ADRs, docs, commit messages, a notes tree. An ask that
contradicts a settled decision is not a coding problem, it is a decision problem,
and the user needs to know before the work starts, not after.

Brief it with: has this been decided, what was decided, what is the status, and
what cites it. Return the record's path and its status field verbatim.

Agent: whatever the project uses for its own knowledge base; `Explore` otherwise.

## 3. What upstream says

Vendor docs, library documentation, release notes, errata, the API reference for
whatever is being used. Distinct from lens 4: this is the authority, not the
crowd.

Brief it with: the specific question, and a demand for a URL and a version on
every claim. A behaviour that changed between versions is the common trap.

Agent: `web-researcher` if defined, otherwise a web-capable agent.

## 4. How other people solve it

Issues, forum threads, other codebases, reference implementations checked out
locally. Weaker evidence than lens 3, and useful for exactly one thing: finding
the failure mode nobody documents.

Brief it with: find at least two independent accounts before reporting a pattern
as common, and label anything single-sourced.

Agent: `web-researcher`, or a codebase-mining agent when the source is local.

## 5. What would break

Callers, tests, config, anything downstream of the thing being changed. This lens
is what turns a three-step plan into an honest one.

Brief it with: list every caller and every test that touches the target, and say
which would need changing. Return counts as well as paths, because "47 callers"
changes the shape of the ask and "2 callers" does not.

Agent: `Explore`, briefed narrowly.

## 6. What it costs

Only when the ask has a performance, memory, or budget dimension. Skip it
otherwise; a speculative cost lens is the most common wasted agent.

Brief it with: find the measured number if one exists, and say plainly when none
does. A measured figure and an estimate are not the same claim and must not be
returned as though they were.

Agent: whatever reads the project's own measurements.

## Choosing

| Shape of ask | Lenses |
|---|---|
| Add a feature | 1, 2, 5 |
| Fix a bug | 1, 5, and 4 if the symptom sounds like a known one |
| Pick between options | 2, 3, 4, 6 |
| Understand something | 1, 2, 3 |
| Make something faster | 1, 6, 5 |
| Adopt a library or tool | 3, 4, 2 |

These are starting points. One good lens beats three padded ones.

## The brief every agent gets

Append this to each, whatever the lens:

> Return conclusions, not contents. Every claim carries a `file:line` or a URL.
> Quote at most a few lines where a passage is the answer. Say plainly what you
> did not cover and why. If the answer is that the thing does not exist, say
> where you looked, because an unqualified "not found" is indistinguishable from
> not having looked.
