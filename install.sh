#!/usr/bin/env bash
# Thin entry point. The logic is in install.py, once, so Linux, macOS and
# Windows run the same code rather than three copies that drift apart.
set -euo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)

for py in python3 python py; do
    if command -v "$py" >/dev/null 2>&1; then
        exec "$py" "$here/install.py" "$@"
    fi
done

echo "claude-kit: no python interpreter found (tried python3, python, py)." >&2
echo "Python 3.11 or newer is required." >&2
exit 1
