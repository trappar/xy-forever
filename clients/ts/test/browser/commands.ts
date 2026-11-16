// Browser commands (run by Node, called from the page through `commands` of 'vitest/browser'): the
// simulator's control port, and its /yaffs2 folder on the Mac, which a page cannot reach itself.
import { mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, statSync, unlinkSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { basename, dirname, join, relative, sep } from 'node:path';
import type { BrowserCommand } from 'vitest/node';
import { control } from '../relay.ts';
import { makeModBase, makeModSet } from '../simulators.ts';

function onMac(root: string, path: string): string {
  if (!path.startsWith('/yaffs2/') || path.split('/').includes('..')) throw new Error(`not a /yaffs2 path: ${path}`);
  return join(root, path.slice('/yaffs2/'.length));
}

const simControl: BrowserCommand<[port: number, line: string]> = (_ctx, port, line) => control(port, line);
const simRead: BrowserCommand<[root: string, path: string]> = (_ctx, root, path) => readFileSync(onMac(root, path)).toString('base64');
const simWrite: BrowserCommand<[root: string, path: string, base64: string]> = (_ctx, root, path, base64) => {
  const file = onMac(root, path);
  mkdirSync(dirname(file), { recursive: true });
  writeFileSync(file, Buffer.from(base64, 'base64'));
};

const simList: BrowserCommand<[root: string, path: string]> = (_ctx, root, path) => readdirSync(onMac(root, path)).sort();

const simDelete: BrowserCommand<[root: string, path: string]> = (_ctx, root, path) => unlinkSync(onMac(root, path));

/** The synthetic mod package set (test/simulators.ts makeModSet): its generation folder's name and files. */
const modSet: BrowserCommand<[]> = () => {
  const work = mkdtempSync(join(tmpdir(), 'xyf-web-mods-'));
  try {
    const gen = makeModSet(work);
    const files = (readdirSync(gen, { recursive: true }) as string[]).filter((p) => statSync(join(gen, p)).isFile())
      .map((p) => ({ path: relative(gen, join(gen, p)).split(sep).join('/'), base64: readFileSync(join(gen, p)).toString('base64') }));
    return { name: basename(gen), files };
  } finally {
    rmSync(work, { recursive: true, force: true });
  }
};

/** What mods.apply needs beside the package set: library entry base and the catalog (test/simulators.ts makeModBase). */
const modBase: BrowserCommand<[root: string, base: string]> = (_ctx, root, base) => makeModBase(root, base);

export const commands = { simControl, simRead, simWrite, simList, simDelete, modSet, modBase };

declare module 'vitest/browser' {
  interface BrowserCommands {
    simControl(port: number, line: string): Promise<Record<string, unknown>>;
    /** A file of the simulator's /yaffs2, base64. */
    simRead(root: string, path: string): Promise<string>;
    simWrite(root: string, path: string, base64: string): Promise<void>;
    /** The names in a folder of the simulator's /yaffs2, sorted. */
    simList(root: string, path: string): Promise<string[]>;
    simDelete(root: string, path: string): Promise<void>;
    /** The synthetic mod package set: the generation folder's name, each file's path in it, base64. */
    modSet(): Promise<{ name: string; files: { path: string; base64: string }[] }>;
    /** Library entry base (synthetic stock os.ldr, info.txt, content.sha256) and content/op1_factory.json in the simulator's /yaffs2. */
    modBase(root: string, base: string): Promise<void>;
  }
}
