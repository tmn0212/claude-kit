# The prompt file

Written to `~/.claude/prompts/<slug>.md`. It is the deliverable, not a summary of
the conversation that produced it.

## The test it has to pass

Somebody opens it in a session that has never seen this conversation, and can
execute it without asking a single question. If a step needs something only this
conversation knows, the file is not finished.

That is also why the findings go in the file rather than a pointer to them. A
`file:line` the reader can open is context; "as we discussed" is not.

## Template

```markdown
# <one line: what gets built or changed>

## The ask

<Two to four sentences. What, where, and why. Written as an instruction to
somebody who will do it, not as a description of a problem.>

## Decided

<Every answer the user gave, in both question rounds, as a flat list of
decisions. Not the questions, the answers. Each one line.>

- Cache lives in Redis, not in process memory.
- Eviction is LRU by last-touch, not by size.

## What is already true

<The findings, each with its coordinate. Only the ones that bear on the work.
This is what makes the file executable from cold.>

- The thumbnail path already opens files through `img_cache.c:210`.
- ADR 0015 fixed the allocation unit at 32 KB; the cache must not assume 4 KB.
- Upstream changed this API in v6.0 (URL), and the v5.5 form is not available.

## Out of scope

<Stated as exclusions, because scope stated as an exclusion is scope that
survives contact with whoever executes it.>

## Done means

<The command, test, or observation that says it worked. One of them, concrete.
"Tests pass" is not concrete unless you name which.>
```

## Rules

**Answers, not questions.** The reader does not need to know what was asked, only
what was settled.

**Every finding carries its coordinate.** No exceptions. A finding without one is
a claim the reader cannot check, and it will be treated as an assumption.

**Nothing hedged.** If something is uncertain, it is either a decision the user
still owes, in which case it goes under "The ask" as an open question, or it is
out of scope. It does not sit in the middle as a maybe.

**No preamble.** No "this document describes". The first heading is the work.

**Keep it under a page** where the ask allows. A prompt file that runs to three
pages has usually swallowed a design doc and should be one.
