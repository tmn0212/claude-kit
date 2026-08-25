"""Per-project configuration for claude-kit.

One file, `claude-kit.toml`, at the project root. Every tool in the kit reads
this module rather than hardcoding a directory name, which is what makes the
same tool work in a firmware repo and a web app.

A project with no config file still works: every key below has a default, and
the defaults describe an ordinary documentation tree.

Discovery walks up from the working directory looking for `claude-kit.toml`,
then for `.git`, then gives up and uses the working directory. That order
matters: a monorepo can put the config beside a sub-package.

Requires Python 3.11 or newer for `tomllib`, which is stdlib.
"""

from __future__ import annotations

import copy
import os
import sys
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - 3.10 and older
    tomllib = None

CONFIG_NAME = "claude-kit.toml"

DEFAULTS: dict = {
    "project": {
        "name": None,  # falls back to the root directory name
    },
    "kb": {
        # Directories indexed for search, relative to the project root.
        "sources": ["docs", "notes", "decisions"],
        "extensions": [".md", ".markdown", ".txt"],
        "skip_dirs": [
            ".git",
            ".venv",
            "node_modules",
            "__pycache__",
            "_out",
            "build",
            "dist",
            "target",
            "vendor",
        ],
        # Query rewrites: typing the left word also searches the right ones.
        "aliases": {},
        # Documents whose stem is this generic get their directory name as the
        # subject instead, so `docs/auth/README.md` is subject `auth`.
        "generic_stems": ["readme", "index", "overview"],
        # Which trees `kb why` treats as evidence a decision rests on. Unset
        # means every source except the decisions directory.
        "evidence_dirs": None,
    },
    "adr": {
        "dir": "docs/decisions",
        "statuses": ["proposed", "accepted", "rejected", "superseded"],
    },
    "promote": {
        "scratch": "tools/scratch",
        "dest": "tools",
        # Where a promoted script gets registered. Empty disables the step.
        "registry": "docs/guides/tooling.md",
    },
    "economics": {
        # Commands whose first word says nothing about what actually ran.
        "multiplexers": ["git", "npm", "npx", "yarn", "pnpm", "cargo", "go", "make", "docker"],
        # Seconds of overhead a single tool call costs. Measured, not assumed:
        # re-derive it for your own machine with `tokencost --calibrate`.
        "penalty_seconds": 1.85,
        # Directories a bare relative path is likely rooted at.
        "root_relative_dirs": ["tools", "docs", "src", "tests"],
    },
    "guards": {
        # A Read larger than this, of an indexed document, is refused.
        "read_bytes": 24000,
        # Context sizes, in thousands of tokens, at which to warn.
        "depth_tiers": [300, 500, 700],
        # An inline interpreter heredoc longer than this is refused.
        "heredoc_lines": 25,
        "enabled": True,
    },
}


def _merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for key, value in over.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def find_root(start: str | os.PathLike | None = None) -> Path:
    """The project root: the nearest ancestor with a config file, else a repo.

    The working directory wins when no start is given. CLAUDE_PROJECT_DIR is
    NOT consulted here on purpose: the hooks pass it explicitly, and letting it
    override the cwd meant that running a tool inside project B during a
    session rooted at project A quietly answered about A.
    """
    here = Path(start or Path.cwd()).resolve()
    if here.is_file():
        here = here.parent
    for candidate in [here, *here.parents]:
        if (candidate / CONFIG_NAME).is_file():
            return candidate
    for candidate in [here, *here.parents]:
        if (candidate / ".git").exists():
            return candidate
    return here


class Config:
    """Resolved configuration, with paths already joined to the root."""

    def __init__(self, root: Path, data: dict):
        self.root = root
        self.data = data

    # -- access ------------------------------------------------------------
    def section(self, name: str) -> dict:
        return self.data.get(name, {})

    def get(self, dotted: str, default=None):
        node = self.data
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def path(self, dotted: str, default=None) -> Path:
        """A configured relative path, joined to the root and normalised.

        Raises on an unset key rather than returning `<root>/None`, which is a
        plausible-looking path that silently does not exist and turns a typo
        into a mystery instead of an error.
        """
        value = self.get(dotted, default)
        if value is None:
            raise KeyError(f"claude-kit: no path configured for '{dotted}'")
        return (self.root / str(value)).resolve()

    @property
    def name(self) -> str:
        return self.get("project.name") or self.root.name

    # -- construction ------------------------------------------------------
    @classmethod
    def load(cls, start=None) -> "Config":
        root = find_root(start)
        path = root / CONFIG_NAME
        # A copy, not the module dict. Handing out DEFAULTS itself means a
        # caller that mutates cfg.data silently changes the defaults for
        # every later load in the same process.
        data = copy.deepcopy(DEFAULTS)
        if path.is_file():
            if tomllib is None:
                sys.stderr.write(
                    f"claude-kit: found {CONFIG_NAME} but this Python "
                    f"({sys.version.split()[0]}) has no tomllib; "
                    "3.11 or newer is required. Using defaults.\n"
                )
            else:
                with open(path, "rb") as handle:
                    # Merge onto a COPY. `_merge` only rebuilds the sections
                    # the file mentions, so every other section would still
                    # be the module's own dict, shared with the next load.
                    data = _merge(copy.deepcopy(DEFAULTS), tomllib.load(handle))
        return cls(root, data)


def load(start=None) -> Config:
    return Config.load(start)


def rel(path: Path, root: Path) -> str:
    """A root-relative path with forward slashes, on every platform.

    Windows `os.path.relpath` returns backslashes. Storing those in an index
    and then testing `startswith("docs/")` is a silent miss, so every stored
    path goes through here.
    """
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()


if __name__ == "__main__":
    cfg = Config.load()
    print(f"root:    {cfg.root}")
    print(f"name:    {cfg.name}")
    print(f"config:  {'present' if (cfg.root / CONFIG_NAME).is_file() else 'defaults'}")
    for key in sorted(cfg.data):
        print(f"[{key}]")
        for sub, value in sorted(cfg.data[key].items()):
            print(f"  {sub} = {value!r}")
    print("CONFIG OK")
