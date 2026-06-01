#!/usr/bin/env sh
# Install project git hooks into .git/hooks (Git Bash / macOS / Linux).
set -eu
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$ROOT/.git/hooks/prepare-commit-msg"
SRC="$ROOT/scripts/git-hooks/prepare-commit-msg"

if [ ! -d "$ROOT/.git" ]; then
  echo 'Not a git repository (no .git directory).' >&2
  exit 1
fi

mkdir -p "$ROOT/.git/hooks"
cat > "$DEST" <<EOF
#!/bin/sh
exec python "$SRC" "\$1" "\$2" "\$3"
EOF
chmod +x "$DEST" "$SRC"
echo "Installed prepare-commit-msg -> $DEST"
