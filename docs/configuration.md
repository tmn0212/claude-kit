# Configuration

One file, `claude-kit.toml`, at the project root.

Every key has a default, so a project with no config file still works. What the
file buys you is the same tools reading your directory names instead of the ones
this kit was extracted from.

`templates/claude-kit.toml` is the annotated copy. This page covers what is not
obvious from it.

## Where the file is found

The tools walk up from the working directory looking for `claude-kit.toml`, then
for a `.git` directory, then fall back to the working directory itself.

That order matters for a monorepo: a config beside a sub-package wins over the
repository root, so each package can index its own documents.

`CLAUDE_PROJECT_DIR` is deliberately NOT consulted during discovery. The hooks
pass it in explicitly, which is the one place it is the right answer. Letting it
override the working directory meant that running a tool inside project B during
a session rooted at project A quietly answered about A.

## The sections

| Section | Read by | What it controls |
|---|---|---|
| `[project]` | all | The display name |
| `[kb]` | `kb`, read guard | Which trees are indexed, and what counts as evidence |
| `[adr]` | `adr`, `kb why` | Where decision records live, and their statuses |
| `[promote]` | `promote`, bash guard | The scratch directory, its destination, and the registry |
| `[economics]` | `tokencost`, `friction` | Command identity and the measured call penalty |
| `[guards]` | every hook the kit installs | Thresholds, and a single switch to turn them all off |
| `[claims]` | `claim`, the claim gate | The Stop hook, the ledger path, and the labels that satisfy it |

## `[kb.aliases]` is the part worth tuning

Full-text search cannot match a word to its synonym. The usual industry answer
is a vector index alongside it, which is not worth it for a few hundred
documents. Twenty lines of aliases recover most of the same recall, cost
nothing, and unlike an embedding you can read the table and correct it when it
is wrong.

```toml
[kb.aliases]
auth = ["authentication", "login", "oauth"]
db   = ["database", "postgres", "sql"]
```

**Keep it to terms your project uses under more than one name.** A general
thesaurus makes every query match everything, which is the same as matching
nothing.

## `evidence_dirs` and what `kb why` joins

`kb why` answers the question actually asked before changing something: why is
it this way, and what would I break.

It joins three things. The decision comes from `adr.dir`. The evidence comes
from any path reference into `kb.evidence_dirs`. The reverse edge, what else
cites this decision, comes from every document in the index that mentions the
record by number.

Leave `evidence_dirs` unset and it means every source except the decisions
directory. Set it when only some of your trees hold evidence:

```toml
[kb]
sources = ["docs", "notes", "benchmarks"]
evidence_dirs = ["notes", "benchmarks"]
```

The reverse edge is the half people forget to build, and it is the half that
says how far a change reaches.

## Turning the guards off

```toml
[guards]
enabled = false
```

That is the kit-wide switch: it silences every hook in every plugin, not only
the refusing ones. `claims.gate = false` turns off the claim gate alone.

Worth knowing before you do: the guards are the enforcement half of the
knowledge base. A rule written in `CLAUDE.md` is a message at the top of the
context window, and by the time a session is deep it is a long way from the
decision it is meant to govern. A hook runs regardless of depth.

If one guard is too aggressive, raise its threshold rather than disabling the
set. `read_bytes` and `heredoc_lines` both exist for that.

## Per-machine overrides

Four environment variables sit outside the config file, because they describe a
machine rather than a project:

| Variable | Effect |
|---|---|
| `KB_NO_AUTOBUILD=1` | `kb` warns that the index is stale instead of rebuilding it |
| `PROSE_PYTHON` | An interpreter that can import the `prose` dependencies |
| `PROSE_DATA` | A directory of Vale rules and thresholds to use instead of the shipped ones |
| `CLAUDE_PROJECT_DIR` | Where the project root is, for the hooks |

## Adding a tool of your own

Follow two conventions and the rest of the kit will cooperate with it.

1. Print a greppable success line on stdout, such as `MYTOOL OK`. Grep for the
   line, not exit code 0.
2. Read `claude-kit.toml` through `kit_config.py` rather than hardcoding a
   directory. `Config.load()` gives you the root and every key.

`promote` does the mechanical half: the executable bit, the shebang, and a row
in the registry document named by `promote.registry`.
