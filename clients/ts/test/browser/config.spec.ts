// In Chrome: the demo page's Mods panel shows the saved configuration (native or legacy) and the
// content state (content-state.txt) against the demo simulator: none, a record, installed, switching, a
// configSource replaced (the seam for config.get); and Build on device is labelled low-level.
import { describe, expect, inject, test } from 'vitest';
import { commands } from 'vitest/browser';
import { load } from './frame.ts';

const web = inject('web');
const sim = web.demo;
const poll = { timeout: 30_000, interval: 100 };
const b64 = (text: string): string => btoa(text);
const DIR = '/yaffs2/xy-forever';
const records = ['config.json', 'config.json.old', 'config.txt', 'config.old', 'content-state.txt', 'content-state.old'];

describe.skipIf(!sim)(`the demo page's saved configuration against the simulator${sim ? '' : ` (skipped: ${web.why})`}`, () => {
  test('no record, a record, content installed and switching, the low-level label', async () => {
    const page = await load(`/demo/index.html?sim=${sim!.port}`);
    const $ = (id: string): HTMLElement => page.getElementById(id)!;
    const done = async (): Promise<string | null> => { await expect.poll(() => $('status').textContent, poll).not.toBe('working'); return $('error').textContent; };
    const remove = async (name: string): Promise<void> => {
      if ((await commands.simList(sim!.root, DIR).catch(() => [] as string[])).includes(name)) await commands.simDelete(sim!.root, `${DIR}/${name}`);
    };
    for (const name of records) await remove(name);
    await expect.poll(() => $('via').textContent, poll).toMatch(/^Simulator on /);
    expect(page.querySelector('#build h3')!.textContent).toBe('Build on device (low-level)');
    expect($('build-hint').textContent).toMatch(/^Low-level action: /);
    try {
      $('connect').click();
      expect(await done()).toBe('');
      expect($('config-saved').textContent).toBe('No saved configuration: no mods.');
      expect($('config-content').textContent).toBe('Content: official.');

      const native = (version: number): string => b64(JSON.stringify({
        schema: 'xy-forever.config', schema_version: version,
        desired: { base: 'opxy-121', profile: 'opxy-1.2.1', generation: 'g1', mods: ['gamma'], memory: 'standard',
          unverified: false }, built: null,
      }));
      await commands.simWrite(sim!.root, `${DIR}/config.json`, native(1));
      $('mods-refresh').click();
      expect(await done()).toBe('');
      expect($('config-saved').textContent).toBe('Base opxy-121 (opxy-1.2.1/g1); mods on: gamma; not built yet.');

      await commands.simWrite(sim!.root, `${DIR}/config.old`, b64('format=1\nbase=opxy-121\nprofile=opxy-1.2.1\ngeneration=g1\nunverified=0\nmods=alpha\n'));
      await commands.simWrite(sim!.root, `${DIR}/config.json.old`, native(1));
      await commands.simWrite(sim!.root, `${DIR}/config.json`, native(2));
      $('mods-refresh').click();
      expect(await done()).toContain('config.json:');
      await remove('config.json');
      $('mods-refresh').click();
      expect(await done()).toBe('');
      expect($('config-saved').textContent).toBe('Base opxy-121 (opxy-1.2.1/g1); mods on: gamma; not built yet.');
      await remove('config.json.old');
      $('mods-refresh').click();
      expect(await done()).toBe('');
      expect($('config-saved').textContent).toBe('Base opxy-121 (opxy-1.2.1/g1); mods on: alpha; not built yet.');

      await commands.simWrite(sim!.root, `${DIR}/config.txt`, b64('format=1\nbase=opxy-121\nprofile=opxy-1.2.1\ngeneration=g1\n' +
        'unverified=1\nbuilt=opxy-121 g1 12345 2\nslot=mods-a\nstamp=' + '0'.repeat(64) + '\nmods=alpha,ext-cap\n'));
      await commands.simWrite(sim!.root, `${DIR}/content-state.txt`, b64('state=installed\nentry=mods-a\nfile=a.json\n'));
      $('mods-refresh').click();
      expect(await done()).toBe('');
      expect($('config-saved').textContent).toBe('Base opxy-121 (opxy-1.2.1/g1), unverified base allowed; mods on: alpha, ext-cap; built into mods-a.');
      expect($('config-content').textContent).toBe('Content: installed for mods-a.');

      await commands.simWrite(sim!.root, `${DIR}/content-state.txt`, b64('state=switching\ntarget=mods-b\n'));
      $('mods-refresh').click();
      expect(await done()).toBe('');
      expect($('config-content').textContent).toBe('Content: needs repair (the switch to mods-b did not finish).');

      await remove('content-state.txt');
      await commands.simWrite(sim!.root, `${DIR}/content-state.old`, b64('state=official\n'));
      $('mods-refresh').click();
      expect(await done()).toBe('');
      expect($('config-content').textContent).toBe('Content: needs repair (a switch did not finish).');

      $('disconnect').click();
      expect(await done()).toBe('');
      expect($('config-saved').textContent).toBe('-');
    } finally {
      for (const name of records) await remove(name);
    }
  });

  test('configSource replaced: the panel shows what the replacement answers', async () => {
    const page = await load(`/demo/index.html?sim=${sim!.port}`);
    const $ = (id: string): HTMLElement => page.getElementById(id)!;
    const done = async (): Promise<string | null> => { await expect.poll(() => $('status').textContent, poll).not.toBe('working'); return $('error').textContent; };
    await expect.poll(() => $('via').textContent, poll).toMatch(/^Simulator on /);
    const win = page.defaultView! as Window & { xyfDemo?: { setConfigSource(f: () => Promise<Record<string, string> | null>): void } };
    await expect.poll(() => win.xyfDemo !== undefined, poll).toBe(true);
    win.xyfDemo!.setConfigSource(async () => ({ base: 'from-config-get', mods: '' }));
    $('connect').click();
    expect(await done()).toBe('');
    expect($('config-saved').textContent).toBe('Base from-config-get; mods on: none; not built yet.');
  });
});
