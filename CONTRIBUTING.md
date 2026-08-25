# Contributing

## Bump the version, or nobody gets your change

Claude Code copies a plugin into `~/.claude/plugins/cache` and decides whether
to update by comparing the `version` string in `plugin.json`. Pushing commits
without bumping that string leaves every existing user on the cached copy, with
no error and no sign anything is wrong.

So: **any change to a plugin's files means bumping that plugin's `version`.**
The four are versioned independently.

## Two more plugin constraints

- A plugin runs from the cache, so a `../` reference out of the plugin directory
  breaks. Use `${CLAUDE_PLUGIN_ROOT}` for anything inside the plugin and
  `${CLAUDE_PLUGIN_DATA}` for anything it needs to write.
- Relative `source` paths in `marketplace.json` do not resolve if somebody adds
  the marketplace by a direct URL to the JSON file. `owner/repo` is the
  supported form, and the README says so.

## Before you push

```sh
python3 tests/smoke.py
prose check --doc README.md
```

`SMOKE OK` and `PROSE OK` are the pass conditions. Grep for the lines, not exit
code 0.

## The success-signal convention

Every tool prints a greppable line on stdout when it finishes its job:
`KB OK`, `ADR OK`, `PROMOTE OK`, `TOKENCOST OK`, `FRICTION OK`, `BRIEF OK`,
`PROSE OK`, `SMOKE OK`.

Keep it in anything new. A tool that half-worked and exited 0 is
indistinguishable from one that worked, and an agent driving these has nothing
else to check.

## Hooks fail open

Every hook in this repo exits 0 with empty stdout on any error: a missing file,
an unparsable payload, a broken import. That rule is not negotiable. A hook runs
before every tool call, so one that can crash is one that can wedge a project.

If you add a guard, add its garbage-payload case to `tests/smoke.py` alongside
the others.

## Adding a config key

Add it to `DEFAULTS` in `kit_config.py` with a comment saying what it is for,
document it in `templates/claude-kit.toml`, and mention it in
`docs/configuration.md` if it is not obvious from the template.

`kit_config.py` is duplicated across plugins on purpose: plugins install as
separate cache copies and cannot import from each other. Change it in
`knowledge-core` and copy it to `session-economics`.

## Style

The Vale rules in `plugins/writing/vale/styles/Minh/` apply to the prose in this
repo too. `prose check --doc <file>` before sending a document.

Comments in the code explain **why**, not what. A comment that restates the line
below it is worse than no comment; a comment that records the failure a line
exists to prevent is the most valuable thing in the file.
