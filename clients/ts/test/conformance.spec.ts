// Every case of protocol/v1.conformance.json through the call layer's link and TCP transport
// against a simulator (test/simulators.ts); the pre-v1 cases: test/pre-v1.spec.ts.
import { beforeAll, describe, expect, inject, test } from 'vitest';
import { loadCases, runCases } from './conformance.ts';
import type { Row } from './conformance.ts';

const sims = inject('sims');
const data = loadCases(undefined, sims.fixtures);
const cases = (data['cases'] as { name: string; variant?: string; needs?: string[] }[]);
const sim = sims.conformance;

describe.skipIf(!sim)(`conformance cases against the simulator${sim ? '' : ` (skipped: ${sims.why})`}`, () => {
  const rows = new Map<string, Row>();
  beforeAll(async () => {
    const result = await runCases(data, sim!.host, sim!.port, sims.fixtures);
    if (result === null) throw new Error(`the simulator at ${sim!.address} does not answer`);
    for (const row of result) rows.set(row.name, row);
  }, 120_000);

  test.each(cases.map((c) => [c.name, c] as const))('%s', (name, c) => {
    const row = rows.get(name)!;
    const fixture = (c.needs ?? []).find((n) => n.startsWith('fixture:') && !(n.slice(8) in sims.fixtures));
    const want = c.variant === 'pre_v1' || fixture ? 'SKIP' : 'PASS';
    expect(`${row.result} ${row.detail}`, row.log.join('\n')).toMatch(new RegExp(`^${want} `));
  });
});
