// A pre-v1 device (the simulator with --pre-v1: build 153's hello, no cmds): the client refuses it
// with "update XY Forever", and the conformance file's pre-v1 cases pass.
import { describe, expect, inject, test } from 'vitest';
import { Client, XyfError } from '../src/index.ts';
import { tcpOpener } from '../src/tcp.ts';
import { loadCases, runCases } from './conformance.ts';

const sims = inject('sims');
const sim = sims.preV1;

describe.skipIf(!sim)(`pre-v1 device${sim ? '' : ` (skipped: ${sims.why})`}`, () => {
  test('connect: update XY Forever', async () => {
    const error = await Client.connect(tcpOpener(sim!.address), { timeoutMs: 5000 }).catch((e: unknown) => e);
    expect(error).toBeInstanceOf(XyfError);
    expect(error).toMatchObject({ code: 'update_required', message: expect.stringMatching(/update XY Forever/) });
  });

  test('the conformance cases for a pre-v1 device', async () => {
    const data = loadCases(undefined, sims.fixtures);
    const rows = await runCases(data, sim!.host, sim!.port, sims.fixtures);
    expect(rows).not.toBeNull();
    const ran = rows!.filter((r) => r.result !== 'SKIP');
    expect(ran.map((r) => r.name)).toEqual(['hello/pre-v1']);
    expect(ran.map((r) => r.result)).toEqual(['PASS']);
  });
});
