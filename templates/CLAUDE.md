# How to talk to me

How answers should read lives in the output style, not here. Do not restate
those rules in this file; two wordings of one rule is dilution, not emphasis.

If you are using the `writing` plugin, that is the **Answer first** style. Turn
it on with `outputStyle` in settings, or `/config`.

# Work

Replace this section with the things about YOUR machine that a session cannot
work out by reading the code. The ones below are examples of the shape.

- Verify which version of a dependency is actually in use before reading its
  source. Stale copies of SDKs and toolchains beside the real one are a common
  trap.
- Say where a tool really lives when there is more than one copy on the machine,
  and say which one is wrong.
- Name the commands that cannot work in a non-interactive shell, and what to use
  instead.
- If I have asked for the same analysis twice, save it as a script. Follow the
  project's own convention for that if it has one.

# Delegating: pick the tier, not just the agent

Effort cannot be set when an agent is called. The `Agent` tool takes `model` but
no `effort`, so the budget lives in the definition file. Choosing the agent IS
choosing the budget.

Agents come in two tiers with the same output contract:

| Thorough | Cheap | Cheap tier is for |
|---|---|---|
| `Explore` | `Explore-quick` | where a known symbol, string or path is |
| `web-researcher` | `web-researcher-quick` | one fact with one obvious source |

**Default to the cheap tier for a lookup, the thorough one for anything that
will inform a decision.** Sources that might disagree, an absence that would be
a finding, or a question that is really "how does this work" all belong in the
thorough tier.

Every cheap agent is told to answer `ESCALATE: <reason>` rather than guess. When
one does, re-ask the thorough agent. That refusal is the tier working, not
failing.

# Checking your own writing

`prose` measures an answer instead of guessing at it. It ships with the
`writing` plugin and lands on the Bash tool's PATH when that plugin is enabled.

| Command | What it does |
|---|---|
| `prose score <file\|->` | Length, grade level, sentence length, bullet density, bold count |
| `prose score --doc <f>` | Same, without the reply-length caps, for a document |
| `prose lint <file>` | Vale against the house rules |
| `prose check <file>` | Score, lint and diagram check in one report. Exit 1 on a hard fail |
| `prose commit <file>` | Check a commit message: subject, body, wrap, trailers |
| `prose diagram <file>` | Validate a Unicode box diagram: width and stroke alignment |
| `prose chart <file>` | `label<TAB>value` lines become an ASCII bar chart |
| `prose base` | Re-derive the corpus baseline from this project's transcripts |
| `prose recent [N]` | Score the last N assistant messages |

Thresholds come from a measured baseline, not taste. The shipped `BASELINE.md`
records what was measured and when; `prose base` re-derives it from your own
transcripts, which is what makes the numbers yours rather than mine.

Use it on anything long before sending it, and on any doc or ADR you write.
