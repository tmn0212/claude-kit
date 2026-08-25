# <project name>

One paragraph: what this is, what state it is in, and the single most important
thing a session should know before touching anything.

## The working rule

State who owns what, in one sentence, and mean it.

> **I write the <X>. Claude is the design partner, not the author.**

Then say what that cashes out to. Be specific about the boundary, because a
vague boundary gets crossed by accident.

**Claude does by default:**

- Design work: options, trade-offs, API sketches inside docs, ending in a
  recommendation. Then stop and let me decide.
- Decision records in `docs/decisions/` for accepted decisions.
- Code review of what I wrote.
- Full ownership of `tools/`, `tests/`, CI, `docs/`.
- Running builds, tests and debugging on request, reporting what actually
  happened.

**Claude does NOT unless asked for that specific thing:** write or edit anything
under `<the directories you own>`; create modules or scaffolding "to get
started"; expand scope past the ask.

**Design conversations produce a written artifact.** A decision that exists only
in a transcript is lost work. Small decision means a record; a subsystem means a
document, then records for the choices inside it.

## Path-scoped rules load themselves

`.claude/rules/` holds the conventions that only matter for one part of the
tree. They load when a matching file is read, so they are not repeated here. A
rule file is markdown with one front-matter key:

```
---
paths:
  - "src/**"
---
```

| Rule | Fires on | Covers |
|---|---|---|
| `code.md` | `src/**` | Style, banned functions, the build check |
| `tests.md` | `tests/**` | The test tiers |
| `docs.md` | `docs/**` | Authoring and the style baseline |

## Where to look

Read the file when the trigger applies. Do not preload. This table is the most
valuable thing in this document: it is what stops a session reading nine files
to answer one question.

| Trigger | Read | Why it matters |
|---|---|---|
| **About to grep or cat a project file to find something** | `kb search <terms>` | **Search before you read.** Then narrow: `kb section <subject> <heading>` returns one section, `kb row <n>` one table row, `kb headings <subject>` the contents. A whole-file read costs several thousand tokens; a section is a fraction of that |
| **About to change something a decision already settled** | `kb why <topic>` | The record, its status, the evidence it rests on, and **what else cites it**. The reverse edge is what says how far a change reaches |
| Wondering what to build next | `docs/roadmap.md`, then `adr open` | `proposed` records ARE the open-questions register |
| **Wondering why a session got expensive** | `tokencost`, `friction` | The bill and the clock, decomposed. Both derive from the transcripts, so neither asserts a number it did not measure |
| Setting up a machine | `docs/guides/setup.md` | |
| Repo layout | `README.md` | |

## Commands

```bash
./tools/build.sh              # quiet build, prints BUILD SUCCESS
./tools/test.sh               # all tiers, prints TESTS OK

kb search <terms>             # indexed search across docs and notes
kb why <topic>                # the decision, its evidence, and who cites it
kb pack <topic>               # one bounded briefing instead of five lookups
adr list | open | new         # decision records. `open` IS the open questions
promote <file>                # scratch script -> tools/, whole ritual in one go
brief                         # where the project stands; runs at session start
tokencost                     # where past sessions burned TOKENS
friction                      # where past sessions burned TIME
prose check <file>            # is this readable? score, lint, diagram check
```

**`BUILD SUCCESS`, `TESTS OK`, `KB OK`, `ADR OK` are the success signals. Grep
for the line, not exit code 0.** That is what makes these safe to drive from an
agent; keep the convention in anything new.

## Environment quirks

Put here the things that have already cost a session an hour. Each line should
name a real failure, not a hypothetical one.

- **Never `cd`. Use absolute paths or `git -C`.** A `cd` breaks every
  repo-relative path after it in the same command, and it persists into later
  calls.
- **The bill is exposure: every token added is re-read by every request after
  it.** Tool results are the largest share. Four habits, in order of what they
  are worth:
  - **Cap what enters the window, as it enters.** `kb pack <topic>` or
    `kb section` rather than reading a whole document.
  - **Never shrink it retroactively.** Rewriting or evicting anything already in
    the window breaks the cache prefix and re-bills the rest.
  - **Batch independent tool calls into one reply.** Every reply re-reads the
    whole window.
  - **Write a document once, then edit it. Scripts by path, never heredocs.**
- Add yours. A quirk that is not written down is a quirk that gets rediscovered.

## Conventions

- **Commits**: short, one line, imperative. Say what changed and why, not that a
  tool was used.
- **When compacting**, preserve open design questions, measured numbers, and the
  options already rejected.
- **No new top-level dependencies without asking.**
