// The Install page's flow (#178; ../pages/install.tsx shows it): installs XY Forever on a stock OP-XY. Its flow, stage names, wording, MTP troubleshooting FAQ,
// "start fresh with OS 1.2.1" path and connection help are Open-XY's builder flow (builder/wizard.mjs and
// builder/index.html, https://github.com/etcetc000/open-xy, staging ca122f4; MIT, see ../vendor/open-xy/LICENSE),
// adapted only where XY Forever differs: the page checks te-boot's identity first and refuses a 1.2.5 bootloader or
// unknown firmware; it ships our os.ldr (pinned) instead of an Open-XY build and, on a Mac, checks that the stock
// os.ldr is there with its 1.2.1 size and the free storage before copying (on Windows the owner looks in Explorer); it never switches the dev menu off (Open-XY's disable and
// recovery-by-disabling are left out). The install ends with Track 5 and a power-on: XY Forever then sets itself up
// on the device (finish.ts), and the page never reads or moves the stock OS. Each stage is a path, /install/<stage> (../routing.ts): a reload keeps it, with the
// choices made so far kept in sessionStorage; a reload re-checks the device instead of trusting them (te-boot stages
// ask te-boot again). At every stage (on load, on each stage change, on a serial port appearing, every few seconds at
// the last stage) and on a click, the page looks for an OP-XY running XY Forever (granted ports only, never the
// chooser unasked) and jumps ahead when it answers; never while a te-boot check over Web MIDI or a WebUSB MTP copy is
// under way (it looks once that ends). te-boot itself is MIDI only, and the serial "OP_XY Boot" is XY Forever's own
// fallback service. Once its stock entry is there and the default names a library entry (the stock one, or one with
// mods) it hands the owner to the app's Mods page; while it sets up, or when it refuses, the page shows that.
import type { Client } from 'xy-forever-client';
import { create } from 'zustand';
import { STOCK_WRONG } from './checks.ts';
import type { Computer, Verdict } from './checks.ts';
import { installState, otherImage } from './finish.ts';
import { InstallStop, TeBoot, copyOverMtp } from './installer.ts';
import type { MidiAccess } from './installer.ts';
import { fetchRelease, pinned } from './release.ts';
import type { ReleasePin } from './release.ts';
import { downloadBlob } from '../lib/download.ts';
import { backDepth, navigatePath as navigate } from '../routing.ts';
import { INSTALL_STAGES } from './stages.ts';
import type { InstallStage } from './stages.ts';

export type How = 'load' | 'push';

/** A connection to XY Forever; close releases it (a no-op for the app's own). */
export interface Lease { readonly client: Client; close(): Promise<void> }

export interface InstallDeps {
  /** navigator.requestMIDIAccess, or null without Web MIDI. */
  readonly midi: MidiAccess | null;
  /** navigator.usb, or null without WebUSB (a Mac then copies as Windows does). */
  readonly usb: unknown;
  /** Web Serial (or the simulator) is available. */
  readonly serial: boolean;
  /** XY Forever over Web Serial on a click: this app's connection when it holds one (never a second link on the
   * port), else a granted port or the chooser (or the simulator). */
  readonly connect: () => Promise<Lease>;
  /** XY Forever is installed: the app takes the connection (kept open) and shows its Mods page. */
  readonly handoff: (lease: Lease) => void;
  /** XY Forever on a port already granted (never the chooser), or null when none answers. */
  readonly find?: () => Promise<Lease | null>;
  /** Calls back when a serial port appears (Web Serial's connect event). */
  readonly onPort?: (listener: () => void) => void;
  readonly release?: ReleasePin | null;
}

const STORE = 'xyf.install';
/** The stages that talk to te-boot over MIDI: a reload asks te-boot again. */
const TE_BOOT = ['menu', 'enable', 'refresh-open', 'refresh-return', 'confirm', 'menu-help', 'computer', 'open-dev-menu'];
export const NO_BACK = ['write-os', 'write-result', 'write-help', 'format-drive', 'format-upload', 'stop', 'start'];
/** How often the last stage looks for XY Forever, and how long a setup that has not started is waited for. */
const LOOK_MS = 3000, POLL_MS = 700, WAIT_START_MS = 60_000;
const STARTED = 'Your OP–XY set up OS 1.2.1 and is starting it.';

/** A stage's button: its label, the stage it leads to, and its own work (id). */
export interface Action { readonly label: string; readonly next: InstallStage | null; readonly id?: string }

/** What the page shows. */
export interface View {
  readonly stage: InstallStage; readonly depth: number; readonly busy: boolean;
  readonly computer: Computer | null; readonly stop: Verdict | null; readonly delivered: boolean; readonly formatting: boolean;
  readonly status: string; readonly error: boolean; readonly fraction: number | null; readonly log: readonly string[];
  /** A Mac with WebUSB: the page copies os.ldr itself. */
  readonly sendsDirectly: boolean;
}

export type InstallFlow = ReturnType<typeof installFlow>;

export function installFlow(deps: InstallDeps) {
  const release = deps.release === undefined ? pinned() : deps.release;
  /** The page is on screen: it looks for XY Forever only then. */
  let shown = false;
  let stage: InstallStage = 'boot';
  let depth = 0;
  const kept = ((): Record<string, unknown> => { try { return JSON.parse(sessionStorage.getItem(STORE) ?? '{}'); } catch { return {}; } })();
  let computer: Computer | null = kept['computer'] === 'mac' || kept['computer'] === 'windows' ? kept['computer'] : null;
  let stop = (kept['stop'] ?? null) as Verdict | null;
  let busy = false, delivered = kept['delivered'] === true, formatting = kept['formatting'] === true;
  /** A look in progress (the probe, then following the setup); the probe alone; a look asked for while busy. */
  let detecting: Promise<void> | null = null, probing: Promise<unknown> | null = null, later = false;
  let status = '', error = false, fraction: number | null = null;
  let log: string[] = [];
  let image: Uint8Array | null = null;
  const teBoot = deps.midi ? new TeBoot(deps.midi, (text) => say(text)) : null;
  const sendsDirectly = (): boolean => computer === 'mac' && deps.usb !== null;
  function snapshot(): View {
    return { stage, depth, busy, computer, stop, delivered, formatting, status, error, fraction, log: [...log], sendsDirectly: sendsDirectly() };
  }
  const view = create<View>(() => snapshot());
  const render = (): void => view.setState(snapshot());

  const say = (text: string, f: number | null = null, isError = false): void => { status = text; fraction = f; error = isError; render(); };
  /** To a stage: a new history entry (back: the stages behind it in the flow); show() then renders it. */
  const go = (next: InstallStage, back: number): void => navigate(`/install/${next}`, { back });
  function advance(next: InstallStage): void {
    if (busy) return;
    go(next, stage === next ? depth : depth + 1);
  }
  function halt(verdict: Verdict): void { stop = verdict; teBoot?.close(); go('stop', 0); }
  function save(): void {
    try { sessionStorage.setItem(STORE, JSON.stringify({ computer, delivered, formatting, stop })); } catch { /* private mode: kept in this page only */ }
  }

  /** Shows the stage a path names (/install/<stage>); an unknown one, or /install, goes to the current stage. */
  function show(path: string, how: How): void {
    const want = INSTALL_STAGES.find((key) => key === path.split('/')[2]);
    if (!want || (want === 'stop' && !stop) || (busy && want !== stage)) {   // back or forward mid-action: stay
      navigate(`/install/${stage}`, { replace: !busy, back: depth });
      return;
    }
    depth = backDepth();
    if (want === 'transfer' && stage !== 'transfer' && how !== 'load') delivered = false;   // back at transfer: send again
    if (want !== stage) { stage = want; status = ''; error = false; fraction = null; log = []; }
    save(); render();
    if (how === 'load' && TE_BOOT.includes(stage)) void act(reverify);   // first: the look waits for it
    void detect();
  }

  /** After a reload at a te-boot stage: te-boot answers again and is still accepted, else back to the start. */
  async function reverify(): Promise<void> {
    if (!teBoot) return;
    say('Checking your OP–XY in te-boot…');
    let verdict: Verdict;
    try { ({ verdict } = await teBoot.check()); } catch {
      busy = false; go('boot', 0);
      say('Your OP–XY did not answer in te-boot. Start it in te-boot again, then check it.', null, true);
      return;
    }
    busy = false;
    if (verdict.kind !== 'ok') halt(verdict); else say('');
  }

  /**
   * Looks for XY Forever on a granted port (never the chooser); when it answers, follows its setup (follow), which
   * jumps ahead. At any stage but stop; while an action runs (te-boot over Web MIDI, the MTP copy) it waits for it.
   */
  function detect(): Promise<void> {
    if (!deps.find || !release || !shown || stage === 'stop') return detecting ?? Promise.resolve();
    if (busy) { later = true; return detecting ?? Promise.resolve(); }
    return detecting ??= (async () => {
      try {
        const lease = await (probing = deps.find!().finally(() => { probing = null; }));
        if (lease) await follow(lease);
      } catch (e) {
        if (e instanceof InstallStop && !!shown) halt(e.verdict);    // other failures: nothing found
      } finally { detecting = null; }
    })();
  }
  deps.onPort?.(() => { for (const ms of [500, 2000, 5000]) setTimeout(() => void detect(), ms); });   // it may answer only after a moment
  setInterval(() => { if (stage === 'start') void detect(); }, LOOK_MS);

  /**
   * XY Forever answered: installed hands the lease to the app (Mods); refused stops with the device's reason; while it
   * sets up (or is about to), shows that at the last stage and asks again until it ends. The device boots OS 1.2.1
   * after setting it up on a plain power-on, so the link may drop: then says so.
   */
  async function follow(lease: Lease): Promise<void> {
    let kept = false, seen = false;
    const since = Date.now();
    try {
      for (;;) {
        let setup;
        try {
          setup = await installState(lease.client, release!);
        } catch (e) {
          if (seen && !lease.client.connected) { say(STARTED); return; }
          throw e;
        }
        if (!shown) return;
        if (setup.kind === 'installed') { kept = true; deps.handoff(lease); return; }
        if (setup.kind === 'refused') { halt(setup.verdict); return; }
        if (stage !== 'start') go('start', 0);
        if (setup.kind === 'waiting' && Date.now() - since > WAIT_START_MS) {
          if (setup.other) { halt(otherImage(lease.client.info.image, release!)); return; }
          say('XY Forever runs, but has not started adding OS 1.2.1. Switch your OP–XY off and on.', null, true); return;
        }
        seen = true;
        say(setup.kind === 'adopting' ? 'Your OP–XY is adding OS 1.2.1 to its library…' : 'Your OP–XY runs XY Forever and is about to add OS 1.2.1 to its library…',
          setup.kind === 'adopting' && setup.total > 0 ? setup.done / setup.total : null);
        await new Promise((resolve) => setTimeout(resolve, POLL_MS));
        if (!lease.client.connected) { say(STARTED); return; }
      }
    } finally {
      if (!kept) await lease.close().catch(() => {});
    }
  }

  /** The owner's click: XY Forever on a granted port, else the browser's chooser. */
  async function findChosen(): Promise<void> {
    say('Looking for XY Forever…');
    await detecting;
    const lease = await deps.connect();
    busy = false;                    // follow may change the stage
    await (detecting = follow(lease).finally(() => { detecting = null; }));
  }

  async function act(work: () => Promise<void>): Promise<void> {
    if (busy) return;
    busy = true; say('');
    try {
      await probing?.catch(() => {});  // a probe under way ends first: none beside te-boot or MTP
      await work();
    } catch (e) {
      busy = false;
      if (e instanceof InstallStop) { halt(e.verdict); return; }
      const message = e instanceof Error ? e.message : String(e);
      if (log.length) log.push(`Stopped: ${message}`);
      say(message, null, true);
    } finally {
      busy = false; render();
      if (later) { later = false; void detect(); }
    }
  }

  const ourImage = async (): Promise<Uint8Array> => (image ??= await fetchRelease(release!));

  function click(a: Action): void {
    if (busy) return;
    switch (a.id) {
      case 'te-check': void act(check); return;
      case 'dev-enable': void act(enable); return;
      case 'mtp-send': void act(send); return;
      case 'download': void act(download); return;
      case 'serial-find': void act(findChosen); return;
      case 'stock-missing': halt(STOCK_WRONG); return;
      case 'recovery-retry': case 'flow-restart':
        formatting = delivered = false; stop = null; go('boot', 0); return;
      case 'format-start': formatting = true; break;
      case 'computer-mac': computer = 'mac'; break;
      case 'computer-windows': computer = 'windows'; break;
    }
    if (a.next) advance(a.next);
  }

  async function check(): Promise<void> {
    say('Connecting to te-boot…');
    const { verdict } = await teBoot!.check();
    busy = false;
    if (verdict.kind !== 'ok') halt(verdict); else advance('menu');
  }

  async function enable(): Promise<void> {
    say('Enabling dev menu…');
    await teBoot!.enableDevMenu();
    busy = false; advance('refresh-open');
  }

  async function send(): Promise<void> {
    log = ['Getting XY Forever…'];
    const data = await ourImage();
    teBoot?.close();                 // te-boot's MIDI is gone in file transfer mode
    say('Choose your OP–XY when your browser asks…');
    await copyOverMtp(deps.usb, data, (text, f) => { if (log.at(-1) !== text) log.push(text); say('', f ?? null); });
    log.push('os.ldr verified on your OP–XY.');
    delivered = true;
  }

  async function download(): Promise<void> {
    teBoot?.close();
    downloadBlob(new Blob([(await ourImage()) as BlobPart]), 'os.ldr');
    say('Downloaded os.ldr. Now drag it to your OP–XY’s root folder.');
  }

  function back(): void { if (!busy && depth > 0) history.back(); }

  function reset(): void {
    stage = 'boot'; depth = 0; computer = null; stop = null; delivered = false; formatting = false;
    status = ''; error = false; fraction = null; log = []; image = null;
    teBoot?.close();
    save(); render();
  }

  return {
    deps, release, view, show, reset, click, back,
    advance,
    setShown: (on: boolean): void => { shown = on; },
  };
}
