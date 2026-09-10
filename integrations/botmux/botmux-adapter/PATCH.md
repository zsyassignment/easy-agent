# BotMux minimal patch

This directory contains the only adapter code that belongs in BotMux. The
Python bridge remains in LearningFlow, so BotMux does not depend on LangGraph,
FastAPI, or the LearningFlow data store.

The example targets BotMux `master` around commit
`f9304ff9f291f7c1b66da8768c6acd13162018bd` (2026-09-03). BotMux evolves
quickly, so use the existing `mira` runner registration as the source of truth
if names move.

## 1. Install the adapter

Copy `learningflow.ts` to:

```text
src/adapters/cli/learningflow.ts
```

The imports intentionally match the adjacent `mira.ts` adapter. For the current
BotMux layout, the included helper applies the core copy/registry/OSC changes
without overwriting an existing adapter:

```bash
bash apply-to-botmux.sh /path/to/botmux
```

Review the resulting diff and run BotMux's typecheck/tests. Setup-menu labels
remain a manual, version-specific addition.

## 2. Add the closed registry entries

Make these mechanical additions next to the existing `mira` entries:

1. In `src/adapters/cli/types.ts`, add `'learningflow'` to the `CliId` union.
2. In the CLI registry, import/export `createLearningFlowAdapter` and add a
   `learningflow` switch/map entry.
3. Add `learningflow` to setup's selectable CLI IDs/display names. A useful
   display label is `LearningFlow Agent`.
4. Add the executable name `learningflow-botmux-bridge` to
   `RAW_CLI_EXECUTABLES`. This makes BotMux's normal binary availability check
   work; use a bot-level `cliPath` when the wrapper is not on the daemon `PATH`.
5. In `src/worker.ts`, add `learningflow` to the runner OSC allowlist:

   ```ts
   const APP_RUNNER_OSC_CLI_IDS = new Set(['mira', 'mir', 'dsh', 'learningflow']);
   ```

   Without this line, the Python process still runs but BotMux treats its OSC
   `final` marker as terminal text and cannot get an authoritative completion.

Search for `mira`, `APP_RUNNER_OSC_CLI_IDS`, `RAW_CLI_EXECUTABLES`, and the
`CliId` union to find all version-specific registration points:

```bash
rg -n "APP_RUNNER_OSC_CLI_IDS|RAW_CLI_EXECUTABLES|CliId|createMira|mira" src
```

## 3. Configure the BotMux bot

Point its CLI path at the wrapper from this repository:

```text
/cloudide/workspace/langgraph-learning-agent/scripts/start-botmux-bridge.sh
```

Or put a symlink named `learningflow-botmux-bridge` on BotMux's `PATH`.
The wrapper loads LearningFlow's `.env`; its default backend is
`http://127.0.0.1:8010`. To override it in the BotMux daemon environment:

```bash
export LEARNINGFLOW_BASE_URL=http://127.0.0.1:8010
```

## Why the standard runner envelope matters

`writeRunnerInput` transports BotMux's immutable turn ID and `trustedCaller`.
The bridge uses the trusted Feishu app/open IDs for a stable private RAG user
namespace, echoes `replyTurnId` in its final OSC marker, and never trusts a
model-visible `<sender>` block for identity.
