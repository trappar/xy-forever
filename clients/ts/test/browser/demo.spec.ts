// In Chrome: the demo page (demo/index.html) with ?sim=, in an iframe of the suite's server, whose
// xyfRelay plugin takes it to the demo simulator; its buttons clicked as a user would.
import { describe, expect, inject, test } from 'vitest';
import { commands } from 'vitest/browser';
import { webSocketOpener } from '../../src/dev.ts';
import { Client } from '../../src/index.ts';
import { load } from './frame.ts';

const web = inject('web');
const sim = web.demo;
const poll = { timeout: 30_000, interval: 100 };

describe.skipIf(!sim)(`the demo page against the simulator${sim ? '' : ` (skipped: ${web.why})`}`, () => {
  test('connect, read a file, screenshot, disconnect', async () => {
    const direct = await Client.connect(webSocketOpener(sim!.relay), { timeoutMs: 10_000 });
    const build = direct.info.build;
    await direct.close();
    const onMac = atob(await commands.simRead(sim!.root, '/yaffs2/firmware.version'));

    const page = await load(`/demo/index.html?sim=${sim!.port}`);
    const $ = (id: string): HTMLElement => page.getElementById(id)!;
    const status = (): string | null => $('status').textContent;
    /** Waits for the action a click started (the click sets `working` at once) to end; then the error text. */
    const done = async (): Promise<string | null> => { await expect.poll(status, poll).not.toBe('working'); return $('error').textContent; };
    await expect.poll(() => $('via').textContent, poll).toMatch(/^Simulator on /);   // demo/main.ts runs

    $('connect').click();
    expect(await done()).toBe('');
    expect(status()).toBe('connected');
    expect($('build').textContent).toBe(String(build));
    expect($('proto').textContent).toMatch(/^1\.\d+$/);
    expect($('mode').textContent).toBe('product');

    ($('path') as HTMLInputElement).value = '/yaffs2/firmware.version';
    $('read').click();
    expect(await done()).toBe('');
    expect($('size').textContent).toBe(`${onMac.length} bytes`);
    expect($('bytes').textContent!.startsWith([...onMac.slice(0, 4)].map((c) => c.charCodeAt(0).toString(16).padStart(2, '0')).join(' '))).toBe(true);

    const canvas = $('screen') as HTMLCanvasElement;
    const colours = (): number => {
      const pixels = canvas.getContext('2d')!.getImageData(0, 0, canvas.width, canvas.height).data;
      const seen = new Set<number>();
      for (let i = 0; i < pixels.length; i += 4) seen.add((pixels[i]! << 24 | pixels[i + 1]! << 16 | pixels[i + 2]! << 8 | pixels[i + 3]!) >>> 0);
      return seen.size;
    };
    expect(colours()).toBe(1);                                     // blank before
    $('screenshot').click();
    expect(await done()).toBe('');
    expect(colours()).toBeGreaterThan(1);                          // the simulator's test pattern

    ($('path') as HTMLInputElement).value = '/yaffs2/no-such-file';
    $('read').click();
    expect(await done()).toMatch(/^not_found: /);
    expect(status()).toBe('connected');

    $('disconnect').click();
    expect(await done()).toBe('');
    expect(status()).toBe('disconnected');
    expect(($('connect') as HTMLButtonElement).disabled).toBe(false);
  });
});
