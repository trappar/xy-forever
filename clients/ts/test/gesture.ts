// The Node side of the Web Serial tests (webserial, chooser and buffer.spec.ts): Chrome's user activation, which
// fake-serial.ts's requestPort() asks for, and the simulator's /yaffs2 folder on the Mac. The fake port is
// plain JavaScript (streams, WebSocket, DOMException), so these tests need no browser; the app in Chrome with the
// same fake is web/e2e/install-sim.e2e.ts.
import { mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';

/** navigator.userActivation as Chrome has it: inactive but while a click's work runs (fromClick). */
const activation = { isActive: false };
Object.defineProperty(navigator, 'userActivation', { value: activation, configurable: true });

/** Runs work as from a real click (a page's Connect button): user activation lasts until work settles. */
export async function fromClick<T>(work: () => Promise<T>): Promise<T> {
  activation.isActive = true;
  try {
    return await work();
  } finally {
    activation.isActive = false;
  }
}

function onMac(root: string, path: string): string {
  if (!path.startsWith('/yaffs2/') || path.split('/').includes('..')) throw new Error(`not a /yaffs2 path: ${path}`);
  return join(root, path.slice('/yaffs2/'.length));
}
/** A file of the simulator's /yaffs2 (root: SimInfo.root). */
export const simRead = (root: string, path: string): Uint8Array => new Uint8Array(readFileSync(onMac(root, path)));
export function simWrite(root: string, path: string, data: Uint8Array): void {
  const file = onMac(root, path);
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, data);
}
