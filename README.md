# claude-kit

A project template for working with [Claude Code](https://claude.com/claude-code).
It gives a new project an indexed knowledge base, decision records, cost
instrumentation, writing control, and tiered subagents, in two commands.

```
┌─ knowledge-core ──────────────────────────────────────────────┐
│  kb       indexed search over your own docs                   │
│  adr      decision records; `proposed` IS the open questions  │
│  promote  a scratch script becomes a tool, in one command     │
├─ session-economics ───────────────────────────────────────────┤
│  tokencost  where sessions burn TOKENS                        │
│  friction   where sessions burn TIME                          │
│  brief      where the project stands, at session start        │
│  + hooks that refuse the expensive shapes                     │
├─ writing ─────────────────────────────────────────────────────┤
│  Answer first output style                                    │
│  prose      measures an answer instead of guessing at it      │
├─ agent-tiers ─────────────────────────────────────────────────┤
│  Explore / Explore-quick, web-researcher / -quick             │
│  prompt-shaper: decide what research an ask is worth          │
├─ claim-gate ──────────────────────────────────────────────────┤
│  a Stop hook that refuses an unlabelled number                │
│  claim      binds a measurement to the code behind it         │
├─ rrun  (vendored, not a plugin) ──────────────────────────────┤
│  zero-escaping remote and cross-shell execution               │
│  the payload rides as base64, never inside a quote            │
└───────────────────────────────────────────────────────────────┘
```

Install what you want; the five plugins are independent. `rrun` is vendored as
a submodule and installs separately: see [docs/rrun.md](docs/rrun.md).

## Install

```sh
claude plugin marketplace add tmn0212/claude-kit
claude plugin install knowledge-core@claude-kit --scope user --config python=python3
```

Repeat for `session-economics`, `agent-tiers` and `claim-gate`. That is the
whole installation: a plugin ships its own skills, agents, output styles, hooks
and a `bin/` directory that joins the Bash tool's PATH while it is enabled.

`writing` takes no `--config`, because it ships no hooks and therefore declares
no options:

```sh
claude plugin install writing@claude-kit --scope user
```

On Windows pass `--config python=py` instead. To change it later, run
`/plugin configure <plugin>@claude-kit` inside a session.

One thing a plugin cannot ship is a user-level `CLAUDE.md`, so that has a script:

```sh
git clone https://github.com/tmn0212/claude-kit
python3 claude-kit/install.py --style
```

It backs up anything it replaces and never touches credentials.

Then, in each project:

```sh
cp claude-kit/templates/claude-kit.toml ./claude-kit.toml
kb build
```

## What each part is for

### knowledge-core

`kb` indexes your markdown into SQLite with full-text search, and returns the
unit you actually want back: a section, a table row, a bounded briefing.

```sh
kb search "cache invalidation"   # full text, aliases expanded
kb section auth "Token refresh"  # ONE section, correctly, where a sed range fails
kb row 1.10                      # ONE table row, paired with its headers
kb pack redis --budget 2500      # one briefing, then it NAMES what it left out
kb why "session store"           # the decision, its evidence, and who cites it
```

`kb why` is the one to reach for before changing something. It joins the
decision to the evidence it rests on and to the reverse edge: what else cites
it, which is what says how far a change reaches.

`adr` manages the decision records. The idea worth stealing is that an **open
question is a record with `status: proposed`**. Same file, same template, filled
in as far as the evidence goes, with the decision left as the question.
Accepting it later is a one-line status change rather than a rewrite, and
`adr open` is the register.

`promote` moves a script out of the scratch directory with the executable bit, a
shebang, and a row in your tooling document. It exists for the economics, not
the mechanics: doing that by hand cost fifteen minutes, doing nothing cost zero,
so nothing is what happened and the next session rewrote the script.

### session-economics

A tool result is not paid once. It stays in the context window and is re-read on
every following request, so its real cost is size times the requests after it.
`tokencost` derives that from the transcripts rather than estimating it, and
`friction` does the same for wall-clock time and for commands that failed and
were never fixed.

The hooks are the enforcement half. Without them the knowledge base is advice.

| Hook | Refuses |
|---|---|
| read guard | An unbounded whole-file Read of a large indexed document, naming the `kb` call instead |
| bash guard | A hand-rolled polling loop, and an interpreter heredoc past a line limit |
| depth guard | Nothing. It reports context depth once per threshold crossed |

Every one fails open. Any error, any unparsable payload, and the tool call
proceeds as normal.

**The friction log holds every command you run.** `log/friction/commands.jsonl`
records the first 400 characters of each Bash call, which routinely catches
tokens passed on a command line and absolute home paths. The plugin writes
`log/.gitignore` the moment it creates that directory, so it excludes itself
before a `git add -A` can reach it. Do not remove that file.

### writing

The **Answer first** output style, and `prose`, which measures a piece of
writing against a baseline: length, grade level, sentence length, bullet
density, bold count, and a Vale pass over seven house rules.

```sh
prose check --doc README.md
```

Thresholds come from a measured corpus, not taste. `prose base` re-derives them
from your own transcripts, which is what makes the numbers yours.

`prose` needs two Python packages; `prose --setup` creates a virtualenv and
installs them. Vale is optional and separate: without it, `prose lint` is
skipped and everything else still works.

### claim-gate

The one gap a survey of existing tooling could not fill: nothing ties a claim to
a measurement. `hyperfine` and Criterion compute a sample size and a spread,
Bencher stores them, and no gate consumes them.

Two halves. A Stop hook refuses to end a turn on a comparative claim in numbers
that never says how the number is known:

```
The aligned path is 1.7x faster than the misaligned one.
   -> blocked: add measured, derived, assumed, or not verified
```

Four things have to hold. A number carrying a unit or an `Nx`. A comparison in
the same sentence. Both outside any code fence. And no label anywhere outside a
fence either.

The comparison test is deliberately conservative, because a false positive
teaches people to disable the hook. A strong comparative fires on its own;
`improved` or `reduced` needs a metric noun beside it, so "I reduced the timeout
from 30s to 10s" is not a claim and "throughput improved to 21.9 MB/s" is.

It fires once per session, so it stays a nudge.

The other half binds a number to the code behind it, and notices when that
moves:

```sh
claim record --id sd.aligned --value 21.9 --unit MB/s \
             --cmd './bench.sh 2.1b' --source src/sd.c --cond 'n=3, A2 card'
claim verify     # CLAIM DRIFT, exit 1, when a named source has changed
```

Content hashes, not timestamps. A checkout or a rebase moves mtime without
changing what the measurement depended on, and a staleness signal that cries
wolf is one people turn off.

The ledger is append-only and belongs in git. A number without its conditions is
noise. A number whose producing code has since changed is worse, because it
still looks like an answer.

It cannot tell whether a label is TRUE. Nothing can, from text alone. It makes
the omission visible, and gives the number somewhere to be checked.

### agent-tiers

Effort cannot be set when an agent is called, so the budget lives in the
definition file. **Choosing the agent IS choosing the budget.** Each agent comes
in two tiers with the same output contract:

| Thorough | Cheap | Cheap tier is for |
|---|---|---|
| `Explore` (sonnet, xhigh) | `Explore-quick` (haiku, low) | where a known symbol or path is |
| `web-researcher` | `web-researcher-quick` | one fact with one obvious source |

Every cheap agent is told to answer `ESCALATE: <reason>` rather than guess. That
refusal is the tier working, not failing.

`prompt-shaper` asks how much research an ask is worth before spending any, then
fans out agents with different lenses and writes a standalone prompt file.

### rrun

The one part that is not a plugin. [rrun](https://github.com/andrewsalinas09/rrun)
is vendored as a submodule at `rrun/`, pinned to a commit, and it installs
itself rather than through the marketplace.

It removes escaping from remote and cross-shell execution. A command that
crosses PowerShell, Git Bash, WSL and a remote shell crosses four parsers, and
counting which one eats each quote fails routinely and fails quietly: the
payload still runs, just not the one you wrote. rrun sends the payload as
base64, which no shell treats as syntax, and only the executing shell decodes
it.

```sh
git submodule update --init rrun     # a plain clone does not fetch it
& .\rrun\install.ps1                 # Windows; needs a real default WSL distro
```

```sh
rrun -s bash HOST -c '...'    # Linux target. The default -s is ps, not bash
rrun -s bash local -c '...'   # replaces a nested powershell -Command
rrun -s bash a,b -c '...'     # replaces ssh a "ssh b ...", re-armored per hop
rrun adb -c '...'             # an Android device over adb shell
rrun -n ...                   # dry run: print the composed command
```

It also ships a `PreToolUse` advisory that warns when a command hand-crosses a
boundary it covers. That hook can collide with a consolidated hook setup, and
its Windows install has three traps worth reading before you hit them.
[docs/rrun.md](docs/rrun.md) covers both.

## Configuration

One file, `claude-kit.toml`, at the project root. Every key has a default, so no
file at all still works. What it buys you is the same tools reading your
directory names.

```toml
[kb]
sources = ["docs", "notes", "decisions"]

[kb.aliases]
auth = ["authentication", "login", "oauth"]

[adr]
dir = "docs/decisions"

[guards]
read_bytes = 24000
```

`templates/claude-kit.toml` documents every key. See
[docs/configuration.md](docs/configuration.md).

## Windows

Everything ships as Python, and the hooks use exec form, which bypasses the
shell entirely. One thing needs your attention:

**Set each plugin's `python` option to `py`.** Windows has `python.exe` and
`py.exe` but no `python3`; Linux and macOS usually have the reverse. Claude Code
substitutes that option into the hook command, which is the only mechanism that
can bridge the two. The default is `python3`.

See [docs/windows.md](docs/windows.md) for the rest, including why
`.gitattributes` matters here.

## Requirements

- Claude Code, any recent version.
- Python 3.11 or newer, for `tomllib`.
- SQLite with FTS5, which is what CPython ships.
- Git, for the parts of `brief` that report repository state.
- Optional: [Vale](https://vale.sh) for `prose lint`. Without it that one step
  is skipped and every other check still runs.
- For `rrun` on Windows only: WSL with a real default distro. See
  [docs/rrun.md](docs/rrun.md). Nothing else in the kit needs WSL.

## Testing

```sh
python3 tests/verify.py
```

Six stages, one signal. `VERIFY OK` is the pass condition, and a failure names
the stage rather than requiring a bisect.

| Stage | What it catches |
|---|---|
| `json` | A manifest that will not parse |
| `validate` | A manifest the harness rejects |
| `shebang` | A launcher with the wrong line endings |
| `cases` | A guard that denies working commands, or misses its target shape |
| `smoke` | A tool that half-works in a project that is not this one |
| `prose` | A document past the readability thresholds |

The two halves run alone as well. `tests/cases.py` drives `tests/cases.toml`,
where **adding a case costs one line**; `tests/smoke.py` drives the lifecycles
that need sequencing.

## A convention worth copying

Every tool prints a greppable success line: `KB OK`, `ADR OK`, `PROMOTE OK`,
`TOKENCOST OK`, `FRICTION OK`, `BRIEF OK`, `PROSE OK`, `SMOKE OK`. **Grep for
the line, not exit code 0.** That is what makes them safe to drive from an agent
session, where a tool that half-worked and exited 0 is indistinguishable from
one that worked.

## Related

Both are vendored here as submodules, pinned to a commit rather than copied.

[claude-statusline-grid](https://github.com/tmn0212/claude-statusline-grid), a
two-row status line that puts its numbers on a fixed grid.

[rrun](https://github.com/andrewsalinas09/rrun), zero-escaping remote and
cross-shell execution. See [docs/rrun.md](docs/rrun.md).

## Licence

MIT.
