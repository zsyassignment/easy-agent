#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
command -v bun >/dev/null 2>&1 || {
  echo "缺少 Bun；请先安装 Bun 1.4.x。" >&2
  exit 1
}
cd "$ROOT/botmux"
bun install --frozen-lockfile
bun run build
echo "BotMux build ready: $ROOT/botmux/dist/cli.js"
