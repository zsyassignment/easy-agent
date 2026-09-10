import { resolveCommand } from './registry.js';
import type { CliAdapter, PtyHandle, WriteInputContext } from './types.js';
import { writeRunnerInput } from './runner-input.js';

/**
 * BotMux adapter for the standalone Python runner shipped by LearningFlow.
 *
 * Install the `learningflow-botmux-bridge` wrapper on PATH, or set the bot's
 * cliPath to /absolute/path/to/langgraph-learning-agent/scripts/start-botmux-bridge.sh.
 */
export function createLearningFlowAdapter(pathOverride?: string): CliAdapter {
  const rawBin = pathOverride ?? 'learningflow-botmux-bridge';
  let cachedBin: string | undefined;
  return {
    id: 'learningflow',
    get resolvedBin(): string { return (cachedBin ??= resolveCommand(rawBin)); },
    allowExtraArgs: false,

    buildArgs({ sessionId }) {
      return ['--session-id', sessionId];
    },

    buildResumeCommand() {
      // LearningFlow conversation state is server-side and keyed by BotMux's
      // stable session id, so there is no separate user-facing resume command.
      return null;
    },

    versionCommand() {
      return { bin: 'printf', args: ['LearningFlow Bridge 0.1.0'] };
    },

    async writeInput(pty: PtyHandle, content: string, context?: WriteInputContext) {
      return writeRunnerInput(
        pty,
        '::botmux-learningflow:',
        content,
        undefined,
        context?.turnId,
        false,
        context?.trustedCaller,
      );
    },

    completionPattern: undefined,
    readyPattern: /›/,
    systemHints: [],
    injectsSessionContext: false,
    supportsTypeAhead: false,
    altScreen: false,
    readOnlyRemoteScroll: false,
  };
}

export const create = createLearningFlowAdapter;
