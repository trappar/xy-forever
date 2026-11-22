import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { XyfError } from 'xy-forever-client';
import type { ErrorCode } from 'xy-forever-client';
import { expect, it } from 'vitest';
import appPackage from '../package.json';
import { BugReport } from '../src/bug-report.ts';
import type { Action } from '../src/bug-report.ts';

const serial = 'XY-UNIT-SERIAL-987';
const report = ['xy-forever', 'reports', 'fake-census.bin'].join('/');
const filename = ['keys', 'synthetic-secret.json'].join('/');
const hostPath = ['/home', 'test-person', 'scratch.mod'].join('/');
const email = ['test-person', 'sample.invalid'].join('@');
const aes = '63'.repeat(16);
const publicKey = 'fac2'.repeat(16);
const safe = { version: '0.1.0', proto: '1.22', image: 'c801e67a', mode: 'product', dev: false };

it('round-trips only allowed fields and passes the repository leak scanner', () => {
  const raw = {
    ...safe,
    serial,
    report,
    filename,
    hostPath,
    email,
    aes,
    publicKey,
    hello: { text: `XYF1;serial=${serial};future=${aes}` },
    rawLog: `opened ${hostPath}`,
  };
  const bugReport = new BugReport();
  bugReport.updateStatus(raw);
  bugReport.record('connect', 'finished');
  bugReport.record(
    'upload_files',
    'failed',
    new XyfError('refused', hostPath, { fields: { filename, aes }, cause: raw }),
  );
  bugReport.record('save_mods', 'failed', new Error(publicKey));
  const snapshot = bugReport.snapshot(true);
  expect(JSON.parse(snapshot)).toEqual({
    bundleVersion: 1,
    appVersion: appPackage.version,
    connected: true,
    device: {
      firmwareVersion: '0.1.0',
      protocolVersion: '1.22',
      image: 'c801e67a',
      mode: 'product',
      developerBuild: false,
    },
    activity: [
      { event: 'connect', outcome: 'finished' },
      { event: 'upload_files', outcome: 'failed', code: 'refused' },
      { event: 'save_mods', outcome: 'failed', code: 'app_error' },
    ],
  });

  // Run the same scanner as the repository on both the rejected inputs and the actual export.
  const scan = spawnSync(
    'uv',
    [
      'run',
      '--quiet',
      'python',
      '-c',
      `
import json, sys
sys.path.insert(0, 'tests')
from test_no_leaks import find_leaks
texts = json.load(sys.stdin)
raw = find_leaks({'raw.json': texts['raw']}, [texts['serial']])
print(json.dumps({
    'raw_rules': sorted({hit.split(': ', 2)[1] for hit in raw}),
    'bundle_hits': find_leaks({'bundle.json': texts['snapshot']}, [texts['serial']]),
}))
`,
    ],
    {
      cwd: fileURLToPath(new URL('../..', import.meta.url)),
      input: JSON.stringify({ raw: JSON.stringify(raw), serial, snapshot }),
      encoding: 'utf8',
      timeout: 10_000,
    },
  );
  expect(scan.error).toBeUndefined();
  expect(scan.status, scan.stderr).toBe(0);
  expect(JSON.parse(scan.stdout)).toEqual({
    raw_rules: ['email', 'evidence path', 'home path', 'key file', 'key-shaped hex', 'private pattern'],
    bundle_hits: [],
  });
});

it('rejects malformed versions, status types, and unrecognized log strings', () => {
  const bugReport = new BugReport();
  bugReport.updateStatus({
    version: '123456.1.0',
    proto: `1.22;${serial}`,
    image: aes,
    mode: `product/${filename}`,
    dev: [report] as unknown as boolean,
  });
  bugReport.record(serial as Action, 'finished');
  bugReport.record('refresh_status', publicKey as 'finished');
  bugReport.record('download_file', 'failed', new XyfError(aes as ErrorCode, hostPath));
  expect(JSON.parse(bugReport.snapshot(false))).toMatchObject({
    connected: false,
    device: { firmwareVersion: null, protocolVersion: null, image: null, mode: null, developerBuild: null },
    activity: [{ event: 'download_file', outcome: 'failed', code: 'app_error' }],
  });
});

it('keeps a bounded activity log and can export before any device connects', () => {
  const bugReport = new BugReport();
  const first = bugReport.snapshot(false);
  bugReport.record('connect', 'failed', new XyfError('timeout', serial));
  for (let i = 0; i < 24; i++) bugReport.record('browse_files', 'finished');
  expect(JSON.parse(first)).toMatchObject({ connected: false, device: null, activity: [] });
  expect(JSON.parse(bugReport.snapshot(false)).activity).toEqual(
    Array.from({ length: 24 }, () => ({ event: 'browse_files', outcome: 'finished' })),
  );
});
