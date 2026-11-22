// What a simulator spec needs beyond the simulator: the verified build of this checkout's sources.
import { test } from '@playwright/test';
import type { Found } from 'xy-forever-client/test/simulators.ts';

/** The verified build's candidate.ldr. Without one the test skips and says why (paths.verified_build()). */
export function verifiedBuild(found: Found): string {
  test.skip(found.why !== null, `skipped: ${found.why}`);
  const path = found.fixtures['xyf_build'];
  if (path) return path;
  const reason = found.noBuild!;
  console.warn(`\n${test.info().title}: skipped: ${reason}\n`);
  test.skip(true, `skipped: ${reason}`);
  return '';
}
