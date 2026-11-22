// The readiness fixture must not turn a broken peer into a passing assertion.
import { expect, test } from 'vitest';
import { waitStartupReady } from './startup.ts';
import * as v1 from '../src/v1.ts';

const hello = (space = true): string =>
  `XYF1;proto=1.21;cmds=${(space ? 1n << BigInt(v1.TYPE['storage.space']) : 2n).toString(16).padStart(32, '0')}`;

test('busy alone retries, and every probe uses the remaining deadline', async () => {
  const replies = [v1.STATUS.busy, v1.STATUS.busy, v1.STATUS.ok];
  const ticks = [0, 100, 200, 300];
  const bounds: number[] = [];
  await waitStartupReady(
    hello(),
    async (remaining) => {
      bounds.push(remaining);
      return replies.shift()!;
    },
    1000,
    () => ticks.shift()!,
  );
  expect(bounds).toEqual([900, 800, 700]);
});

test('permanently busy expires and other statuses fail immediately', async () => {
  const ticks = [0, 100, 900, 1100];
  let calls = 0;
  await expect(
    waitStartupReady(
      hello(),
      async () => {
        calls++;
        return v1.STATUS.busy;
      },
      1000,
      () => ticks.shift()!,
    ),
  ).rejects.toThrow('deadline');
  expect(calls).toBe(2);
  for (const status of [v1.STATUS.link, v1.STATUS.storage, v1.STATUS.unsupported]) {
    calls = 0;
    await expect(
      waitStartupReady(hello(), async () => {
        calls++;
        return status;
      }),
    ).rejects.toThrow('storage.space:');
    expect(calls).toBe(1);
  }
});

test('transport failures propagate and older peers receive no probe', async () => {
  await expect(
    waitStartupReady(hello(), async () => {
      throw new Error('disconnected');
    }),
  ).rejects.toThrow('disconnected');
  for (const text of [hello(false), 'XYF1;proto=1']) {
    await waitStartupReady(text, async () => {
      throw new Error('unexpected readiness probe');
    });
  }
});
