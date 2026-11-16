// In Chrome: the demo page's Mods panel against the demo simulator, with the synthetic package set
// (test/simulators.ts makeModSet) chosen as a folder (webkitRelativePath): nothing held, choose, Send
// (byte-identical on the device, the summary), the list, Send again (all skipped), an interrupted
// package flagged, Remove (refused once, then confirmed). Build on device: with the set and a base entry
// in the simulator's storage, choose machines, an extension and the base, Build (the result line, library
// entry mods-a with an os.ldr, tmp/ empty), an unknown base unconfirmed (the plain reason), Make default
// (refused once, then confirmed: the content switch, the default written), Cancel mid-build (A slowed;
// the other panels wait). confirm() is answered by the test.
import { describe, expect, inject, test } from 'vitest';
import { commands } from 'vitest/browser';
import { load } from './frame.ts';

const web = inject('web');
const sim = web.demo;
const poll = { timeout: 30_000, interval: 100 };

describe.skipIf(!sim)(`the demo page's Mods panel against the simulator${sim ? '' : ` (skipped: ${web.why})`}`, () => {
  test('choose a package folder, send it, list it, send again, remove a package', async () => {
    const set = await commands.modSet();
    const info = Object.fromEntries(atob(set.files.find((f) => f.path === 'base/info.txt')!.base64).split('\n')
      .filter((l) => l.includes('=')).map((l) => [l.slice(0, l.indexOf('=')), l.slice(l.indexOf('=') + 1)]));
    const gen = `/yaffs2/xy-forever/mods/${info['profile']}/${info['generation']}`;
    const packages = [...new Set(set.files.map((f) => f.path.split('/')[0]!))].sort();

    const page = await load(`/demo/index.html?sim=${sim!.port}`);
    const win = page.defaultView! as Window & typeof globalThis;
    let confirmAnswer = true;
    const asked: string[] = [];
    win.confirm = (text?: string): boolean => { asked.push(text ?? ''); return confirmAnswer; };
    const $ = (id: string): HTMLElement => page.getElementById(id)!;
    const done = async (): Promise<string | null> => { await expect.poll(() => $('status').textContent, poll).not.toBe('working'); return $('error').textContent; };
    const rows = (): HTMLTableRowElement[] => [...page.querySelectorAll<HTMLTableRowElement>('#mods-list tbody tr')].filter((r) => r.dataset['name']);
    const row = (name: string): HTMLTableRowElement | undefined => rows().find((r) => r.dataset['name'] === name);
    await expect.poll(() => $('via').textContent, poll).toMatch(/^Simulator on /);

    $('connect').click();
    expect(await done()).toBe('');
    expect($('mods-list').textContent).toBe('No mod packages on the device.');
    expect(($('mods-send') as HTMLButtonElement).disabled).toBe(true);

    const picked = new win.DataTransfer();                          // as a webkitdirectory input gives them
    for (const f of set.files) {
      const file = new win.File([Uint8Array.from(atob(f.base64), (c) => c.charCodeAt(0))], f.path.split('/').pop()!);
      Object.defineProperty(file, 'webkitRelativePath', { value: `${set.name}/${f.path}` });
      picked.items.add(file);
    }
    const input = $('mods-folder') as HTMLInputElement;
    expect(input.webkitdirectory).toBe(true);
    input.files = picked.files;
    input.dispatchEvent(new win.Event('change'));
    expect(await done()).toBe('');
    expect($('mods-chosen').textContent).toMatch(new RegExp(`^Chosen: ${info['profile']}/${info['generation']}, ${packages.length} packages, ${set.files.length} files, `));

    $('mods-send').click();
    expect(await done()).toBe('');
    expect(asked.at(-1)).toMatch(new RegExp(`^Send ${packages.length} packages of ${info['profile']}/${info['generation']} to /yaffs2/xy-forever/mods/ on the OP-XY\\?`));
    expect($('mods-progress').textContent).toMatch(new RegExp(`^Sent ${packages.length} packages, skipped 0: ${set.files.length} files, .* in [0-9.]+ s$`));
    for (const f of set.files) expect(await commands.simRead(sim!.root, `${gen}/${f.path}`), f.path).toBe(f.base64);
    expect(rows().map((r) => r.dataset['name']).sort()).toEqual(packages);
    expect(rows().every((r) => r.dataset['complete'] === 'true')).toBe(true);
    expect(row('base')!.cells[1]!.textContent).toMatch(/^mod-base/);
    expect($('mods-list').querySelector('tr.generation')!.textContent).toMatch(
      new RegExp(`^${info['profile']}/${info['generation']}: ${packages.length} packages, ${set.files.length} files, `));

    $('mods-send').click();                                         // the same set again: nothing goes over
    expect(await done()).toBe('');
    expect($('mods-progress').textContent).toMatch(new RegExp(`^Sent 0 packages, skipped ${packages.length}: 0 files, 0 B in `));

    await commands.simDelete(sim!.root, `${gen}/gamma/files.sha256`);   // as an interrupted push leaves it
    $('mods-refresh').click();
    expect(await done()).toBe('');
    expect(row('gamma')!.dataset['complete']).toBe('false');
    expect(row('gamma')!.textContent).toContain('incomplete: send again');

    const remove = (): void => [...row('alpha')!.querySelectorAll('button')].find((b) => b.textContent === 'Remove')!.click();
    confirmAnswer = false;                                          // the user says no: nothing changes
    remove();
    expect(await done()).toBe('');
    expect(asked.at(-1)).toMatch(new RegExp(`^Remove the package alpha \\(\\d+ files, .*\\) of ${info['profile']}/${info['generation']} from the OP-XY\\?$`));
    expect(await commands.simList(sim!.root, gen)).toContain('alpha');
    confirmAnswer = true;
    remove();
    expect(await done()).toBe('');
    expect(await commands.simList(sim!.root, gen)).not.toContain('alpha');
    expect(row('alpha')).toBeUndefined();
    expect($('mods-progress').textContent).toBe(`Removed alpha of ${info['profile']}/${info['generation']}`);

    $('disconnect').click();
    expect(await done()).toBe('');
    expect(rows()).toEqual([]);
  });

  test('Build on device: choose, build, see the result; a failure in words; cancel', async () => {
    const set = await commands.modSet();
    const info = Object.fromEntries(atob(set.files.find((f) => f.path === 'base/info.txt')!.base64).split('\n')
      .filter((l) => l.includes('=')).map((l) => [l.slice(0, l.indexOf('=')), l.slice(l.indexOf('=') + 1)]));
    const gen = `/yaffs2/xy-forever/mods/${info['profile']}/${info['generation']}`;
    for (const f of set.files) await commands.simWrite(sim!.root, `${gen}/${f.path}`, f.base64);
    await commands.modBase(sim!.root, 'modbase');

    const page = await load(`/demo/index.html?sim=${sim!.port}`);
    const win = page.defaultView! as Window & typeof globalThis;
    const asked: string[] = [];
    let confirmAnswer = true;
    win.confirm = (text?: string): boolean => { asked.push(text ?? ''); return confirmAnswer; };
    const $ = (id: string): HTMLElement => page.getElementById(id)!;
    const done = async (): Promise<string | null> => { await expect.poll(() => $('status').textContent, poll).not.toBe('working'); return $('error').textContent; };
    const choice = (id: string): HTMLInputElement => page.querySelector<HTMLInputElement>(`#build-choices tr[data-id="${id}"] input`)!;
    await expect.poll(() => $('via').textContent, poll).toMatch(/^Simulator on /);
    $('connect').click();
    expect(await done()).toBe('');

    expect(($('build-generation') as HTMLSelectElement).value).toBe(`${info['profile']}/${info['generation']}`);
    const bases = [...($('build-base') as HTMLSelectElement).options].map((o) => o.value);
    expect(bases).toEqual(['modbase', 'opxy-121', 'opxy-121-stock']);
    const rows = [...page.querySelectorAll<HTMLTableRowElement>('#build-choices tbody tr')];
    expect(rows.map((r) => [r.dataset['id'], r.dataset['kind'], r.textContent])).toEqual([
      ['alpha', 'machine', ' Alphasynth'], ['beta', 'machine', ' Betaeffect'], ['gamma', 'machine', ' Gammaplayer'],
      ['delta', 'machine', ' Deltasynth'], ['omega', 'machine', ' Omegasynth'], ['cap', 'extension', ' Captureextension'],
      ['flt', 'extension', ' Filterextension']]);
    ($('build-base') as HTMLSelectElement).value = 'modbase';
    choice('alpha').checked = true;
    choice('gamma').checked = true;
    choice('cap').checked = true;

    $('build-start').click();                                       // the base is not a known one, unconfirmed
    expect(await done()).toBe('');
    expect(asked.at(-1)).toBe(`Build "OP-XY mods" on the OP-XY from modbase with alpha, gamma, cap (${info['profile']}/${info['generation']})? ` +
      'The result replaces the older of the Firmware library entries mods-a and mods-b (never the default).');
    expect($('build-result').textContent).toMatch(/^Build failed: the base is not a known stock firmware/);

    ($('build-unverified') as HTMLInputElement).checked = true;
    expect(($('build-default') as HTMLButtonElement).disabled).toBe(true);   // nothing built yet
    $('build-start').click();
    expect(await done()).toBe('');
    expect($('build-result').textContent).toMatch(/^built: mods-a is in the Firmware library \(content not installed\); [0-9.]+ s, \d+ steps$/);
    expect(await commands.simList(sim!.root, '/yaffs2/xy-forever/firmware/mods-a')).toContain('os.ldr');
    expect(await commands.simList(sim!.root, '/yaffs2/xy-forever/tmp')).toEqual([]);   // committed, nothing left in tmp/
    expect($('build-progress').textContent).toMatch(/^Building on the device: job \d+, done/);

    expect(($('build-default') as HTMLButtonElement).disabled).toBe(false);
    confirmAnswer = false;
    $('build-default').click();                                     // refused: nothing sent
    expect(await done()).toBe('');
    expect(asked.at(-1)).toMatch(/^Make mods-a the default firmware\? The OP-XY first installs its content: it rewrites files in \/yaffs2\/content\//);
    expect(await commands.simList(sim!.root, '/yaffs2/xy-forever')).not.toContain('content-state.txt');
    confirmAnswer = true;
    $('build-default').click();
    expect(await done()).toBe('');
    expect($('build-result').textContent).toBe('mods-a is the default firmware (its content is installed)');
    expect($('build-progress').textContent).toMatch(/^Installing the content of mods-a: done/);
    expect(await commands.simList(sim!.root, '/yaffs2/xy-forever')).toContain('content-state.txt');

    await commands.simControl(sim!.control, 'set a_slow_ms 4');
    try {
      $('build-start').click();
      await expect.poll(() => $('build-progress').textContent, poll).toMatch(/^Building on the device: job \d+, (?!idle)/);
      expect(($('build-cancel') as HTMLButtonElement).disabled).toBe(false);
      expect(($('files-refresh') as HTMLButtonElement).disabled).toBe(true);   // the other panels wait
      expect(($('mods-refresh') as HTMLButtonElement).disabled).toBe(true);
      $('build-cancel').click();
      expect(await done()).toBe('');
      expect($('build-result').textContent).toBe('Cancelled: the OP-XY removed what it wrote');
      expect(($('build-cancel') as HTMLButtonElement).disabled).toBe(true);
    } finally {
      await commands.simControl(sim!.control, 'set a_slow_ms 0');
    }
    expect(await commands.simList(sim!.root, '/yaffs2/xy-forever/tmp')).toEqual([]);
    $('files-refresh').click();                                     // storage commands are served again
    expect(await done()).toBe('');
    $('disconnect').click();
    expect(await done()).toBe('');
  });
});
