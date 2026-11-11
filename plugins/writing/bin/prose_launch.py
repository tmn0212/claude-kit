#!/usr/bin/env python3
"""claude-kit: prose. Measures an answer instead of guessing at it.

This launcher exists because the engine has real dependencies, and the one
place they cannot go is the interpreter Claude Code happens to run with. It
resolves an interpreter in this order:

1. `$PROSE_PYTHON`, if set.
2. A virtualenv beside the plugin, or in the user cache.
3. The current interpreter, if the dependencies happen to be importable.

If none of those work it says exactly what to run, and `prose --setup` does it.
Nothing here installs anything without being asked.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
ENGINE = PLUGIN / "lib" / "prose_impl.py"
REQUIREMENTS = PLUGIN / "requirements.txt"

# The user cache comes FIRST, and that order is the whole point. An installed
# plugin lives at .../writing/<version>/, so a venv created beside it is
# destroyed by the next `claude plugin update` and has to be rebuilt every
# release. One venv outside the version tree is shared by every version, and
# the requirements almost never change.
#
# The plugin-local path stays as a second choice for a dev checkout that
# already has one, so an existing `.venv` is still found rather than ignored.
VENVS = [
    Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache")) / "claude-kit" / "prose",
    PLUGIN / ".venv",
]


def venv_python(root: Path) -> Path:
    """The interpreter inside a venv, on whichever platform this is.

    POSIX puts it in `bin`, Windows in `Scripts`. Getting this wrong is the
    single most common way a cross-platform tool fails on Windows.
    """
    if os.name == "nt":
        return root / "Scripts" / "python.exe"
    return root / "bin" / "python"


def has_deps(python: str | Path) -> bool:
    try:
        result = subprocess.run(
            [str(python), "-c", "import textstat, plotext"],
            capture_output=True,
            timeout=30,
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def find_interpreter() -> str | None:
    explicit = os.environ.get("PROSE_PYTHON")
    if explicit and has_deps(explicit):
        return explicit
    for root in VENVS:
        candidate = venv_python(root)
        if candidate.exists() and has_deps(candidate):
            return str(candidate)
    if has_deps(sys.executable):
        return sys.executable
    return None


def setup() -> int:
    """Create a virtualenv and install the engine's dependencies into it."""
    for root in VENVS:
        try:
            root.parent.mkdir(parents=True, exist_ok=True)
            print(f"creating {root}")
            subprocess.run([sys.executable, "-m", "venv", str(root)], check=True)
            python = venv_python(root)
            subprocess.run(
                [str(python), "-m", "pip", "install", "--quiet", "-r", str(REQUIREMENTS)],
                check=True,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            print(f"  could not use {root}: {exc}")
            continue
        if has_deps(python):
            print(f"prose is ready: {python}")
            print("PROSE SETUP OK")
            return 0
    print("prose: could not create a usable virtualenv in any candidate location", file=sys.stderr)
    return 1


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--setup":
        return setup()

    python = find_interpreter()
    if python is None:
        sys.stderr.write(
            "prose: the engine's dependencies are not installed.\n\n"
            "  prose --setup        create a virtualenv and install them\n\n"
            f"Or install them yourself from {REQUIREMENTS} and set PROSE_PYTHON\n"
            "to an interpreter that can import textstat and plotext.\n\n"
            "Vale is separate and optional: without it, `prose lint` is skipped\n"
            "and everything else still works. See https://vale.sh for install.\n"
        )
        return 2

    try:
        return subprocess.run([python, str(ENGINE), *sys.argv[1:]]).returncode
    except (OSError, subprocess.SubprocessError) as exc:
        sys.stderr.write(f"prose: could not run the engine: {exc}\n")
        return 2


if __name__ == "__main__":
    sys.exit(main())
