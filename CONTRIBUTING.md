# Contributing

## Bump the version, or nobody gets your change

Claude Code copies a plugin into `~/.claude/plugins/cache` and decides whether
to update by comparing the `version` string in `plugin.json`. Pushing commits
without bumping that string leaves every existing user on the cached copy, with
no error and no sign anything is wrong.

So: **any change to a plugin's files means bumping that plugin's `version`.**
The five are versioned independently.

## Two more plugin constraints

- A plugin runs from the cache, so a `../` reference out of the plugin directory
  breaks. Use `${CLAUDE_PLUGIN_ROOT}` for anything inside the plugin and
  `${CLAUDE_PLUGIN_DATA}` for anything it needs to write.
- Relative `source` paths in `marketplace.json` do not resolve if somebody adds
  the marketplace by a direct URL to the JSON file. `owner/repo` is the
  supported form, and the README says so.

## Before you push

```sh
python3 tests/verify.py
```

`VERIFY OK` is the pass condition, and it covers the prose check too. Grep for
the line, not exit code 0.

Run it BEFORE saying the work is done. Every bug that reached a commit here got
there the same way: the work was announced, and only then verified.

## The success-signal convention

Every tool prints a greppable line on stdout when it finishes its job:
`KB OK`, `ADR OK`, `PROMOTE OK`, `CLAIM OK`, `TOKENCOST OK`,
`FRICTION OK`, `BRIEF OK`, `PROSE OK`. The test harness adds `CASES OK`,
`SMOKE OK` and `VERIFY OK`.

Keep it in anything new. A tool that half-worked and exited 0 is
indistinguishable from one that worked, and an agent driving these has nothing
else to check.

## Hooks fail open

Every hook in this repo exits 0 with empty stdout on any error: a missing file,
an unparsable payload, a broken import. That rule is not negotiable. A hook runs
before every tool call, so one that can crash is one that can wedge a project.

If you add a guard, add its cases to `tests/cases.toml`. That is one line per
case, which is the point: the reason cases used to be added after a bug rather
than before was that each one meant writing a test function.

Raise the group's floor in `tests/cases.py` when you add cases. Never lower one
to make a run pass: the floors exist because emptying the table used to print
`CASES OK`.

## Adding a config key

Add it to `DEFAULTS` in `kit_config.py` with a comment saying what it is for,
document it in `templates/claude-kit.toml`, and mention it in
`docs/configuration.md` if it is not obvious from the template.

`kit_config.py` is duplicated across plugins on purpose: plugins install as
separate cache copies and cannot import from each other. There are four copies,
in `knowledge-core`, `session-economics`, `claim-gate` and `agent-tiers`. Change
the first and copy it to the other three; `tests/smoke.py` asserts they match.

## Style

The Vale rules in `plugins/writing/vale/styles/Minh/` apply to the prose in this
repo too. `prose check --doc <file>` before sending a document.

Comments in the code explain **why**, not what. A comment that restates the line
below it is worse than no comment; a comment that records the failure a line
exists to prevent is the most valuable thing in the file.
