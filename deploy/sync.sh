#!/usr/bin/env bash
# usage: deploy/sync.sh <ssh-host> [remote-dir, relative to remote home]
set -euo pipefail

host="${1:?usage: deploy/sync.sh <ssh-host> [remote-dir]}"
dir="${2:-tape/app}"
root="$(cd "$(dirname "$0")/.." && pwd)"

ssh "$host" "mkdir -p '$dir'"
rsync -az --delete --exclude-from="$root/deploy/rsync-exclude" "$root/" "$host:$dir/"
ssh "$host" "export PATH=\$HOME/.local/bin:\$PATH && cd '$dir' && uv sync --frozen --no-dev && uv run --no-dev tape env"
ssh "$host" "systemctl is-enabled --quiet tape-api 2>/dev/null && sudo systemctl restart tape-api tape-ui || true"
