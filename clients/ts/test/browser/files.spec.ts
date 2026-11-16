// In Chrome: the demo page's Files panel against the demo simulator (as demo.spec.ts, which leaves
// it disconnected): browse, download and compare, upload by picker and by drop, new folder, delete,
// rename and copy (a file, and a folder by copyTree), the write-area hint, and a refused delete in upgrade/
// showing the reason. confirm() and prompt() are answered by the test.
import { describe, expect, inject, test } from 'vitest';
import { commands } from 'vitest/browser';
import { load } from './frame.ts';

const web = inject('web');
const sim = web.demo;
const poll = { timeout: 30_000, interval: 100 };
const DIR = '/yaffs2/content/web-files';

const toBase64 = (b: Uint8Array): string => btoa(String.fromCharCode(...b));
const fromBase64 = (s: string): Uint8Array => Uint8Array.from(atob(s), (c) => c.charCodeAt(0));
function random(n: number): Uint8Array<ArrayBuffer> {
  const out = new Uint8Array(n);
  for (let at = 0; at < n; at += 65536) crypto.getRandomValues(out.subarray(at, Math.min(n, at + 65536)));
  return out;
}

describe.skipIf(!sim)(`the demo page's Files panel against the simulator${sim ? '' : ` (skipped: ${web.why})`}`, () => {
  test('browse, download, upload (picker and drop), new folder, delete, rename, copy, a refused delete', async () => {
    const seeded = random(3000);
    await commands.simWrite(sim!.root, `${DIR}/seed.bin`, toBase64(seeded));
    await commands.simWrite(sim!.root, `${DIR}/a-folder/inner.txt`, btoa('inner'));

    const page = await load(`/demo/index.html?sim=${sim!.port}`);
    const win = page.defaultView! as Window & typeof globalThis;
    let confirmAnswer = true;
    const asked: string[] = [];
    win.confirm = (text?: string): boolean => { asked.push(text ?? ''); return confirmAnswer; };
    let promptAnswer = 'new-folder';
    const prompted: (string | undefined)[] = [];
    win.prompt = (_text?: string, value?: string): string => { prompted.push(value); return promptAnswer; };
    const $ = (id: string): HTMLElement => page.getElementById(id)!;
    const status = (): string | null => $('status').textContent;
    const done = async (): Promise<string | null> => { await expect.poll(status, poll).not.toBe('working'); return $('error').textContent; };
    const rows = (): HTMLTableRowElement[] => [...page.querySelectorAll<HTMLTableRowElement>('#files-list tbody tr')];
    const row = (name: string): HTMLTableRowElement | undefined => rows().find((r) => r.dataset['name'] === name);
    /** A row's button: its label, or 'open' for a folder's name. */
    const click = (name: string, label: string): void => {
      [...row(name)!.querySelectorAll('button')].find((b) => label === 'open' ? b.className === 'open' : b.textContent === label)!.click();
    };
    const crumbs = (): string => $('crumbs').textContent!;
    await expect.poll(() => $('via').textContent, poll).toMatch(/^Simulator on /);

    $('connect').click();                                            // connect lists /yaffs2
    expect(await done()).toBe('');
    expect(crumbs()).toBe('yaffs2');
    const kinds = rows().map((r) => r.dataset['kind']);
    expect(kinds.lastIndexOf('folder')).toBeLessThan(kinds.indexOf('file'));   // folders first
    expect(row('firmware.version')!.dataset['size']).toBe(String(atob(await commands.simRead(sim!.root, '/yaffs2/firmware.version')).length));

    click('content', 'open');
    expect(await done()).toBe('');
    click('web-files', 'open');
    expect(await done()).toBe('');
    expect(crumbs()).toBe('yaffs2 / content / web-files');
    expect(rows().map((r) => r.dataset['name'])).toEqual(['a-folder', 'seed.bin']);
    expect(row('seed.bin')!.cells[1]!.textContent).toBe('2.9 KiB');
    expect(row('a-folder')!.cells[1]!.textContent).toBe('-');
    expect($('files-hint').textContent).toBe('Changes under /yaffs2/, never /yaffs2/bootloader.dev or /yaffs2/upgrade.');

    click('seed.bin', 'Download');
    expect(await done()).toBe('');
    const link = $('files-saved').querySelector('a')!;
    expect(link.download).toBe('seed.bin');
    expect(new Uint8Array(await (await win.fetch(link.href)).arrayBuffer())).toEqual(seeded);

    const picked = random(200_000);
    const pickedFiles = new win.DataTransfer();
    pickedFiles.items.add(new win.File([picked], 'picked.bin'));
    const input = $('files-upload') as HTMLInputElement;
    input.files = pickedFiles.files;
    input.dispatchEvent(new win.Event('change'));
    expect(await done()).toBe('');
    expect(asked.at(-1)).toMatch(/^Write picked\.bin \(195\.3 KiB\) to \/yaffs2\/content\/web-files\/ /);
    expect(fromBase64(await commands.simRead(sim!.root, `${DIR}/picked.bin`))).toEqual(picked);
    expect(row('picked.bin')!.dataset['size']).toBe('200000');
    expect($('files-progress').textContent).toBe('Uploaded picked.bin (195.3 KiB)');

    const dropped = new win.DataTransfer();
    dropped.items.add(new win.File(['dropped text\n'], 'dropped.txt'));
    $('files-drop').dispatchEvent(new win.DragEvent('drop', { dataTransfer: dropped, bubbles: true, cancelable: true }));
    expect(await done()).toBe('');
    expect(atob(await commands.simRead(sim!.root, `${DIR}/dropped.txt`))).toBe('dropped text\n');
    expect(rows().map((r) => r.dataset['name'])).toEqual(['a-folder', 'dropped.txt', 'picked.bin', 'seed.bin']);

    $('files-mkdir').click();
    expect(await done()).toBe('');
    expect(row('new-folder')!.dataset['kind']).toBe('folder');
    expect(await commands.simList(sim!.root, DIR)).toContain('new-folder');

    confirmAnswer = false;                                            // the user says no: nothing changes
    click('picked.bin', 'Delete');
    expect(await done()).toBe('');
    expect(await commands.simList(sim!.root, DIR)).toContain('picked.bin');
    confirmAnswer = true;
    click('picked.bin', 'Delete');
    expect(await done()).toBe('');
    expect(asked.at(-1)).toBe(`Delete the file ${DIR}/picked.bin from the OP-XY? This cannot be undone.`);
    click('new-folder', 'Delete');
    expect(await done()).toBe('');
    expect(await commands.simList(sim!.root, DIR)).toEqual(['a-folder', 'dropped.txt', 'seed.bin']);
    expect(rows().map((r) => r.dataset['name'])).toEqual(['a-folder', 'dropped.txt', 'seed.bin']);

    promptAnswer = `${DIR}/renamed.txt`;                              // Rename/Move: the prompt starts at the path
    click('dropped.txt', 'Rename/Move');
    expect(await done()).toBe('');
    expect(prompted.at(-1)).toBe(`${DIR}/dropped.txt`);
    expect(atob(await commands.simRead(sim!.root, `${DIR}/renamed.txt`))).toBe('dropped text\n');
    promptAnswer = `${DIR}/seed-copy.bin`;                            // Copy a file: the same bytes
    click('seed.bin', 'Copy');
    expect(await done()).toBe('');
    expect(prompted.at(-1)).toBe(`${DIR}/seed.bin`);
    expect(fromBase64(await commands.simRead(sim!.root, `${DIR}/seed-copy.bin`))).toEqual(seeded);
    expect($('files-progress').textContent).toBe(`Copied ${DIR}/seed.bin to ${DIR}/seed-copy.bin (2.9 KiB)`);
    promptAnswer = `${DIR}/b-folder`;                                 // Copy a folder: copyTree
    click('a-folder', 'Copy');
    expect(await done()).toBe('');
    expect(atob(await commands.simRead(sim!.root, `${DIR}/b-folder/inner.txt`))).toBe('inner');
    expect(rows().map((r) => r.dataset['name'])).toEqual(['a-folder', 'b-folder', 'renamed.txt', 'seed-copy.bin', 'seed.bin']);
    click('seed.bin', 'Copy');                                       // onto an existing name: the device's reason
    expect(await done()).toBe(`state: file.copy ${DIR}/seed.bin -> ${DIR}/b-folder: the target exists already (nothing replaced)`);

    click('a-folder', 'Delete');                                     // not empty: the device's reason
    expect(await done()).toBe(`state: file.rmdir ${DIR}/a-folder: the folder is not empty: remove what it holds first`);

    // Since 1.13 only bootloader.dev and upgrade/ are closed to hand changes: the stock updater's inbox is read-only.
    await commands.simWrite(sim!.root, '/yaffs2/upgrade/old.bin', btoa('old'));
    [...$('crumbs').querySelectorAll('button')].find((b) => b.textContent === 'yaffs2')!.click();
    expect(await done()).toBe('');
    expect($('files-hint').textContent).toBe('Changes under /yaffs2/, never /yaffs2/bootloader.dev or /yaffs2/upgrade.');
    click('upgrade', 'open');
    expect(await done()).toBe('');
    expect($('files-hint').textContent).toBe('Changes under /yaffs2/, never /yaffs2/bootloader.dev or /yaffs2/upgrade: this folder is read-only.');
    click('old.bin', 'Delete');
    expect(await done()).toBe('refused: file.delete /yaffs2/upgrade/old.bin: the OP-XY changes files only under ' +
      '/yaffs2/, never /yaffs2/bootloader.dev or /yaffs2/upgrade (or inside it)');
    expect(row('old.bin')).toBeDefined();
    expect(status()).toBe('connected');

    $('disconnect').click();
    expect(await done()).toBe('');
    expect(rows()).toEqual([]);
  });
});
