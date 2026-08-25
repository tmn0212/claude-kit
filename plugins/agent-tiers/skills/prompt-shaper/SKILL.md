---
name: prompt-shaper
description: Turn a rough ask into an executable one at a pace the user picks. Use when the prompt-shaper hook says to, or when the user asks to shape, scope, or think through a request before doing it. Asks the pace first, fans out up to five research agents, asks follow-up questions grounded in what they found, and on the deeper paces writes the shaped prompt to a file it can hand to a clean session. Everything scales to the pace, including doing none of it.
---

# Prompt shaper

Rough ask in, executable ask out. The user chooses how much gets spent getting
there, before anything is spent.

## The one rule

**Pace is the user's decision, not yours.** Every other step in this file reads
the pace and does less or more. You never upgrade a pace because the problem
looks interesting, and you never downgrade one because it looks easy.

## Step 1: pace

Skip this step only when the hook already named a pace, or the user did.

Ask exactly one `AskUserQuestion`, four options, in this order. Put the real
numbers in the descriptions so the choice is informed:

| Label | Description to show |
|---|---|
| Now | No research, no questions. I execute on what you wrote. |
| Quick | 1 agent, about 30 seconds. Nothing further asked. |
| Normal | Up to 3 agents in parallel, 1 to 2 minutes. 2 to 4 questions after the research. |
| Deep | Up to 5 agents, 2 to 4 minutes. A full round of questions after the research, and a prompt file. |

Header: `Pace`. `multiSelect: false`.

If the ask is genuinely unscoped, and only then, add **one** second question in
the same call: which of two or three targets is meant. Nothing else. Everything
else waits for the research, because asking it now wastes the research.

On `Now`, stop reading this file and execute the original request.

## Step 2: fan out

| Pace | Agents | Effort |
|---|---|---|
| Quick | 1 | inherits the agent's own definition |
| Normal | up to 3 | as defined |
| Deep | up to 5 | as defined |

Send them **in one message** so they run concurrently. Sequential agents at this
count are the difference between 40 seconds and 4 minutes.

Give each a different lens, never the same brief repeated. The catalogue is in
[references/lenses.md](references/lenses.md); drop any lens the ask has no angle
for, so a question with nothing on the web spends 3 agents rather than 5.

Prefer a purpose-built agent over `general-purpose` when one exists in this
project. Measured here, over 241 runs: 50 tool calls and 2.8M cache-read per run,
and a loose brief is what spends them. Bound every brief.

Every agent is told, in its own prompt: return conclusions with `file:line` or a
URL, never raw file contents, and say what you did not cover.

Before spawning, print one line: how many agents, which lenses, and the rough
cost. After they return, print what it actually was. A cap you do not report
reads as coverage that never happened.

## Step 3: the second round

Step 1 was round one: the pace, and at most one scoping question. This is round
two, and it is the only other round there is.

`Quick` skips it entirely, so a `Quick` run asks nothing beyond the pace. On
`Normal`, 2 to 4 questions. On `Deep`, the same round carried further, because
this is where the substance is.

Ask only what the findings surfaced: the forks the agents actually found, the
places where two options are both real. These questions are impossible to ask
before the research, which is exactly why they are the valuable ones.

Every option must come from a finding, and must name it. "Option A, because
`board.c:88` already does this" is a grounded option. "Option A, simpler" is not.

Do not re-ask anything the user has already answered, in this session or in
step 1.

## Step 4: the prompt file

On `Deep`, always. On `Normal`, when the user asks. Never on `Quick` or `Now`.

Write to `~/.claude/prompts/<slug>.md`, where `<slug>` is a short kebab-case name
for the ask. The template is in [references/prompt-file.md](references/prompt-file.md).

The file must stand alone. Somebody reading it in a fresh session, with no memory
of this conversation, must be able to execute it. That is the whole point: a plan
is consumed once, a prompt file survives the clear.

## Step 5: the handoff

Print the path, then offer three, in this order:

1. Run it here, now. Context is already warm.
2. `/clear`, then paste the file. Cleanest execution, loses this conversation.
3. Leave it for later.

You cannot clear context yourself. Say the command, do not pretend to run it.

## Step 6: the ledger

Append one JSON line to `~/.claude/prompt-shaper/ledger.jsonl` on every run that
got past step 1:

```json
{"pace":"deep","agents":5,"lenses":["codebase","prior-decisions","web"],"rounds":2,"file":"add-thumb-cache","accepted":true}
```

Create the directory if it does not exist. This is what lets the pace thresholds
be tuned from data later instead of from taste. A failure to write the ledger is
never a failure of the run: log it and carry on.

## What makes this go wrong

**Asking the pace on something trivial.** Worse than not having the skill. The
hook's routing line already tells you to skip trivial asks and follow-ups; take
it.

**Two agents with the same brief.** Pure waste. If two lenses would read the same
files, they are one lens.

**Questions the research could have answered.** Every question in round 2 that a
file could have answered is a question that should have been an agent.

**Shaping an ask the user has already scoped.** If the prompt already says what,
where and how it will be checked, there is nothing to shape. Say so and execute.

**Silent escalation.** Spending 5 agents on a `Quick` is a broken promise about
cost, which is the one thing this skill exists to keep.
