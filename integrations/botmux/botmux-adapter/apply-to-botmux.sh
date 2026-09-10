#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
  echo "Usage: $0 /path/to/botmux" >&2
  exit 2
fi

ROOT="$(cd "$1" && pwd)"
SELF="$(cd "$(dirname "$0")" && pwd)"
for file in \
  "$ROOT/src/adapters/cli/types.ts" \
  "$ROOT/src/adapters/cli/registry.ts" \
  "$ROOT/src/worker.ts"; do
  [[ -f "$file" ]] || { echo "Not a compatible BotMux checkout: missing $file" >&2; exit 1; }
done

TARGET="$ROOT/src/adapters/cli/learningflow.ts"
if [[ -e "$TARGET" ]]; then
  echo "Refusing to overwrite existing $TARGET" >&2
  exit 1
fi
cp "$SELF/learningflow.ts" "$TARGET"

BOTMUX_ROOT="$ROOT" python3 - <<'PY'
from pathlib import Path
import os

root = Path(os.environ["BOTMUX_ROOT"])

def replace_once(path: Path, old: str, new: str) -> None:
    text = path.read_text(encoding="utf-8")
    if new in text:
        return
    if text.count(old) != 1:
        raise SystemExit(f"Expected exactly one registration anchor in {path}: {old!r}")
    path.write_text(text.replace(old, new), encoding="utf-8")

registry = root / "src/adapters/cli/registry.ts"
replace_once(
    registry,
    "import { createMiraAdapter } from './mira.js';",
    "import { createMiraAdapter } from './mira.js';\nimport { createLearningFlowAdapter } from './learningflow.js';",
)
replace_once(registry, "  mira: undefined,", "  mira: undefined,\n  learningflow: 'learningflow-botmux-bridge',")
replace_once(
    registry,
    "createHermesAdapter, createMiraAdapter, createMirAdapter",
    "createHermesAdapter, createMiraAdapter, createLearningFlowAdapter, createMirAdapter",
)
replace_once(
    registry,
    "    case 'mira': return createMiraAdapter(pathOverride);",
    "    case 'mira': return createMiraAdapter(pathOverride);\n    case 'learningflow': return createLearningFlowAdapter(pathOverride);",
)

types = root / "src/adapters/cli/types.ts"
text = types.read_text(encoding="utf-8")
if "'learningflow'" not in text:
    anchor = " | 'mira' |"
    if anchor not in text:
        raise SystemExit("Could not find CliId union's mira anchor")
    types.write_text(text.replace(anchor, " | 'mira' | 'learningflow' |", 1), encoding="utf-8")

worker = root / "src/worker.ts"
text = worker.read_text(encoding="utf-8")
if "APP_RUNNER_OSC_CLI_IDS" not in text:
    raise SystemExit("Could not find APP_RUNNER_OSC_CLI_IDS in src/worker.ts")
if "'learningflow'" not in text and '"learningflow"' not in text:
    if '"dsh"' in text:
        text = text.replace('"dsh"', '"dsh", "learningflow"', 1)
    elif "'dsh'" in text:
        text = text.replace("'dsh'", "'dsh', 'learningflow'", 1)
    else:
        raise SystemExit("Could not find the dsh OSC allowlist anchor")
    worker.write_text(text, encoding="utf-8")
PY

echo "Applied core LearningFlow adapter registration."
echo "Run BotMux typecheck/tests, then add LearningFlow to setup's display choices if desired."
