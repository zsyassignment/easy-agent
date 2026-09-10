import { Buffer } from 'node:buffer';
import { describe, expect, it } from 'vitest';
import { createLearningFlowAdapter } from '../src/adapters/cli/learningflow.js';
import type { PtyHandle } from '../src/adapters/cli/types.js';

function decodeWrite(value: string): Record<string, unknown> {
  const prefix = '::botmux-learningflow:';
  expect(value.endsWith('\r')).toBe(true);
  expect(value.startsWith(prefix)).toBe(true);
  return JSON.parse(Buffer.from(value.slice(prefix.length, -1), 'base64').toString('utf8'));
}

describe('LearningFlow adapter', () => {
  it('starts the external bridge with the BotMux session id', () => {
    const adapter = createLearningFlowAdapter('/opt/learningflow-bridge');
    expect(adapter.id).toBe('learningflow');
    expect(adapter.resolvedBin).toBe('/opt/learningflow-bridge');
    expect(adapter.buildArgs({ sessionId: 'session-1', resume: false })).toEqual([
      '--session-id', 'session-1',
    ]);
    expect(adapter.allowExtraArgs).toBe(false);
    expect(adapter.versionCommand?.()).toEqual({
      bin: 'printf',
      args: ['LearningFlow Bridge 0.1.0'],
    });
    expect(adapter.readyPattern?.test('LearningFlow connected.\n› ')).toBe(true);
  });

  it('keeps turn correlation and trusted caller in the runner envelope', async () => {
    const writes: string[] = [];
    const pty: PtyHandle = { write(value) { writes.push(value); } };
    const adapter = createLearningFlowAdapter('/opt/learningflow-bridge');
    const result = await adapter.writeInput(pty, '请结合附件回答', {
      turnId: 'turn-1',
      trustedCaller: {
        requestUserOpenId: 'ou_test',
        requestLarkAppId: 'cli_test',
        senderType: 'user',
      },
    });

    expect(result).toMatchObject({ submitted: true });
    expect(writes).toHaveLength(1);
    expect(decodeWrite(writes[0]!)).toEqual({
      type: 'message',
      content: '请结合附件回答',
      replyTurnId: 'turn-1',
      trustedCaller: {
        requestUserOpenId: 'ou_test',
        requestLarkAppId: 'cli_test',
        senderType: 'user',
      },
    });
  });
});
