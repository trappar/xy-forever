// The Install page (#178): installs XY Forever on a stock OP-XY. Its flow, stage names, wording, MTP troubleshooting FAQ,
// "start fresh with OS 1.2.1" path and connection help are Open-XY's builder flow (builder/wizard.mjs and
// builder/index.html, https://github.com/etcetc000/open-xy, staging ca122f4; MIT, see ../vendor/open-xy/LICENSE),
// adapted only where XY Forever differs: the page checks te-boot's identity first and refuses a 1.2.5 bootloader or
// unknown firmware; it ships our os.ldr (pinned) instead of an Open-XY build and checks the stock os.ldr and the free
// storage before copying; it never switches the dev menu off (Open-XY's disable and recovery-by-disabling are left out);
// and it finishes over Web Serial after a PLAY start. #page-install's data-stage names the stage (the tests read it).
// Each stage is a path, /install/<stage> (../router.ts): a reload keeps it, with the choices made so far kept in
// sessionStorage; a reload re-checks the device instead of trusting them (te-boot stages ask te-boot again; the
// finish stage looks for XY Forever). At every stage, on a serial port appearing and on a click, the page looks for an
// OP-XY running XY Forever (granted ports only, never the chooser unasked) and jumps to the finish, or to done.
import type { Client } from 'xy-forever-client';
import { ASK_TEXT, CHOOSE_TEXT } from 'xy-forever-client';
import { OFFICIAL_121_TFW, OFFICIAL_UPDATE_GUIDE } from './checks.ts';
import type { Computer, Verdict } from './checks.ts';
import { finishInstall, installState } from './finish.ts';
import { InstallStop, TeBoot, checkDroppedStock, copyOverMtp } from './installer.ts';
import type { MidiAccess } from './installer.ts';
import { fetchRelease, pinned } from './release.ts';
import type { ReleasePin } from './release.ts';
import { downloadBlob, h } from '../ui.ts';
import { backDepth, navigate } from '../router.ts';
import type { How } from '../router.ts';

/** A connection to XY Forever; close releases it (a no-op for the app's own). */
export interface Lease { readonly client: Client; close(): Promise<void> }

export interface InstallDeps {
  /** navigator.requestMIDIAccess, or null without Web MIDI. */
  readonly midi: MidiAccess | null;
  /** navigator.usb, or null without WebUSB (a Mac then copies as Windows does). */
  readonly usb: unknown;
  /** Web Serial (or the simulator) is available. */
  readonly serial: boolean;
  /** Connects to XY Forever over Web Serial (the chooser, or the simulator). */
  readonly connect: () => Promise<Client>;
  /** XY Forever on a port already granted (never the chooser), or null when none answers. */
  readonly find?: () => Promise<Lease | null>;
  /** Calls back when a serial port appears (Web Serial's connect event). */
  readonly onPort?: (listener: () => void) => void;
  readonly release?: ReleasePin | null;
}

const STORE = 'xyf.install';
/** The stages that talk to te-boot over MIDI: a reload asks te-boot again. */
const TE_BOOT = ['menu', 'enable', 'refresh-open', 'refresh-return', 'confirm', 'menu-help', 'computer', 'open-dev-menu'];
const NO_BACK = ['write-os', 'write-result', 'write-help', 'format-drive', 'format-upload', 'stop', 'done'];

interface Action { readonly label: string; readonly next: string | null; readonly id?: string }
type Stage = [title: string, detail: string, actions: Action[], instructions?: string[]];
const action = (label: string, next: string | null, id?: string): Action => (id ? { label, next, id } : { label, next });

export function installPage(root: HTMLElement, deps: InstallDeps): { render: () => void; show: (path: string, how: How) => void } {
  const release = deps.release === undefined ? pinned() : deps.release;
  let stage = 'boot', depth = 0;
  const kept = ((): Record<string, unknown> => { try { return JSON.parse(sessionStorage.getItem(STORE) ?? '{}'); } catch { return {}; } })();
  let computer: Computer | null = kept['computer'] === 'mac' || kept['computer'] === 'windows' ? kept['computer'] : null;
  let stop = (kept['stop'] ?? null) as Verdict | null;
  let busy = false, delivered = kept['delivered'] === true, stockChecked = kept['stockChecked'] === true, formatting = kept['formatting'] === true;
  let finished = false;
  let detecting: Promise<void> | null = null;
  let status = '', error = false, fraction: number | null = null;
  let log: string[] = [];
  let image: Uint8Array | null = null;
  const teBoot = deps.midi ? new TeBoot(deps.midi, (text) => say(text)) : null;
  const sendsDirectly = (): boolean => computer === 'mac' && deps.usb !== null;

  const say = (text: string, f: number | null = null, isError = false): void => { status = text; fraction = f; error = isError; render(); };
  /** To a stage: a new history entry (back: the stages behind it in the flow); show() then renders it. */
  const go = (next: string, back: number): void => navigate(`/install/${next}`, { back });
  function advance(next: string): void {
    if (busy) return;
    go(next, stage === next ? depth : depth + 1);
  }
  function halt(verdict: Verdict): void { stop = verdict; teBoot?.close(); go('stop', 0); }
  function save(): void {
    try { sessionStorage.setItem(STORE, JSON.stringify({ computer, delivered, stockChecked, formatting, stop })); } catch { /* private mode: kept in this page only */ }
  }

  /** Shows the stage a path names (/install/<stage>); an unknown one, or /install, goes to the current stage. */
  function show(path: string, how: How): void {
    const want = path.split('/')[2] ?? '';
    if (!(want in table()) || (want === 'stop' && !stop) || (busy && want !== stage)) {   // back or forward mid-action: stay
      navigate(`/install/${stage}`, { replace: !busy, back: depth });
      return;
    }
    depth = backDepth();
    if (want !== stage) { stage = want; status = ''; error = false; fraction = null; log = []; }
    save(); render();
    void detect();
    if (how === 'load' && TE_BOOT.includes(stage)) void act(reverify);
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

  /** Looks for XY Forever on a granted port (never the chooser); when it answers, jumps to the finish or to done. */
  function detect(): Promise<void> {
    if (!deps.find || !release || busy || root.hidden || ['stop', 'done'].includes(stage)) return detecting ?? Promise.resolve();
    return detecting ??= (async () => {
      try {
        const lease = await deps.find!();
        if (!lease) return;
        try {
          const state = await installState(lease.client);
          if (!busy && !root.hidden) jump(state);
        } finally { await lease.close().catch(() => {}); }
      } catch (e) {
        if (e instanceof InstallStop && !busy && !root.hidden) halt(e.verdict);   // other failures: nothing found
      } finally { detecting = null; }
    })();
  }
  deps.onPort?.(() => { for (const ms of [500, 2000, 5000]) setTimeout(() => void detect(), ms); });   // it may answer only after a moment

  function jump(state: 'finish' | 'done'): void {
    if (state === 'done') { finished = false; go('done', 0); return; }
    if (stage !== 'finish') go('finish', 0);
    say('Your OP–XY is running XY Forever. Select Connect and finish.');
  }

  /** The owner's click: XY Forever on a granted port, else the browser's chooser. */
  async function findChosen(): Promise<void> {
    say('Looking for XY Forever…');
    await detecting;
    const client = await deps.connect();
    let state: 'finish' | 'done';
    try { state = await installState(client); } finally { await client.close().catch(() => {}); }
    busy = false; jump(state);
  }

  async function act(work: () => Promise<void>): Promise<void> {
    if (busy) return;
    busy = true; say('');
    try {
      await work();
    } catch (e) {
      busy = false;
      if (e instanceof InstallStop) { halt(e.verdict); return; }
      const message = e instanceof Error ? e.message : String(e);
      if (log.length) log.push(`Stopped: ${message}`);
      say(message, null, true);
    } finally {
      busy = false; render();
    }
  }

  const ourImage = async (): Promise<Uint8Array> => (image ??= await fetchRelease(release!));

  /** Open-XY's stage table, adapted: [title, detail, actions, instructions]. */
  function table(): Record<string, Stage> {
    const transferSteps = ['Open your OP–XY in File Explorer.', 'Select Download os.ldr below.', 'Drag os.ldr to your OP–XY’s root folder.',
      'Confirm when asked to replace the file.', `Check that os.ldr shows about ${release ? Math.ceil(release.bytes / 1024) : '?'} KB, not 0 KB.`];
    const official = ['Switch off. Hold COM while switching on.', 'Press track 1: “upload firmware”, then connect USB.',
      'Copy the downloaded 1.2.1 .tfw to the removable disk, then eject it.', 'Wait for the update to finish, then select Start again.'];
    const stages: Record<string, Stage> = {
      boot: ['Start in the te-boot menu.', 'The page first checks that your OP–XY can take XY Forever. It changes nothing until then.',
        [action(busy ? 'Checking…' : 'Connected and at te-boot · Check my OP–XY', null, 'te-check')],
        ['Power off your OP–XY.', 'Hold COM while switching it on.', 'Release COM when the te-boot menu appears.', 'Connect it to this computer with USB.',
          'Allow MIDI access if asked.']],
      menu: ['Does your screen show "3. dev menu"?', 'Check the te-boot menu on your OP–XY.', [action('No, it’s missing', 'enable', 'menu-no'), action('Yes, I see it', 'computer', 'menu-yes')]],
      enable: ['Enable the dev menu.', 'Keep your OP–XY at te-boot.', [action(busy ? 'Enabling…' : 'Enable dev menu', null, 'dev-enable')], [
        'Select Enable dev menu below. Allow MIDI access if asked.', 'XY Forever leaves the dev menu on: it is how your OP–XY can always be recovered.']],
      'refresh-open': ['Open the system menu.', 'On your OP–XY, press track 8 for "8. system menu".', [action('System menu is open', 'refresh-return')]],
      'refresh-return': ['Return to te-boot.', 'Press track 8 for "8. back" to refresh the te-boot menu.', [action('I’m back at te-boot', 'confirm')]],
      confirm: ['Does your screen show "3. dev menu"?', 'Check the te-boot menu on your OP–XY.', [action('Yes, I see it', 'computer', 'dev-confirm'), action('No, it’s still missing', 'menu-help')]],
      'menu-help': ['The dev menu is still missing.', 'Keep your OP–XY at te-boot and retry enabling it, or use Connection help below.',
        [action(busy ? 'Enabling…' : 'Retry enabling dev menu', null, 'dev-enable'), action('Refresh the screen again', 'refresh-open')]],
      computer: ['Which computer are you using?', 'The device has a transfer option for each system.', [action('Windows', 'open-dev-menu', 'computer-windows'), action('Mac', 'open-dev-menu', 'computer-mac')]],
      'open-dev-menu': ['Open the dev menu.', 'Press track 3 for "3. dev menu". Your OP–XY calls this screen "factory menu".', [action('Dev menu is open', 'start-transfer')]],
      'start-transfer': ['Start file transfer.', computer === 'mac' ? 'Press track 2 for "2. mtp file transfer (mac)".' : 'Press track 3 for "3. mtp file transfer (windows)".',
        [action('I see "mtp mode active"', sendsDirectly() ? 'close-field-kit' : 'stock')]],
      'close-field-kit': ['Quit Field Kit and OpenMTP.', 'These apps can hold your OP–XY’s USB connection. Quit Field Kit, OpenMTP and Android File Transfer if any are running, then continue.',
        [action('They’re closed · Continue', 'transfer', 'field-kit-closed')]],
      stock: ['Check the original OS file.', 'The page reads it on this computer only: nothing is uploaded.', [action('Skip: check it after the first start', 'transfer', 'stock-skip')],
        ['In File Explorer, open your OP–XY, then upgrade › firmware › blackfin › bin.', 'Drag os.ldr from there onto the box below.',
          'If dragging does not work, copy it to your desktop first and choose it.']],
      transfer: sendsDirectly()
        ? ['Send XY Forever to your OP–XY.', 'The page checks the original OS file and the free storage, copies os.ldr to the root and reads it back. Your device’s other content stays in place.',
          delivered ? [action('File sent · Continue', 'exit-transfer')] : [action(busy ? 'Sending…' : log.length ? 'Try again' : 'Send to OP–XY', null, 'mtp-send')]]
        : ['Copy the OS file.', 'Close any File Explorer windows showing your OP–XY’s other folders. Keep your device’s other content in place.',
          [action('Download os.ldr', null, 'download'), action('File copied · Continue', 'exit-transfer')], transferSteps],
      'exit-transfer': ['Exit file transfer.', 'Once the file has finished copying, press track 2 for "2. exit" to return to the dev menu (titled "factory menu" on your OP–XY).', [action('I’m back at the dev menu', 'write-os')]],
      'write-os': ['Write the OS.', 'Press track 5 for "5. write os to boot partition". Keep your OP–XY connected to power.', [action('I pressed track 5', 'write-result')]],
      'write-result': ['Wait for the write result.', 'Don’t power off while writing. When "firmware written ok!" appears under "looking for /os.ldr", it’s safe to power cycle your OP–XY.',
        [action('I see "firmware written ok!"', 'finish'), action('I see something else', 'write-help')]],
      'write-help': ['Check the message on your OP–XY.', 'If it stops at "looking for /os.ldr" without "firmware written ok!", os.ldr isn’t at the device’s top level. For any other message, press track 5 again and wait; don’t power off until "firmware written ok!" appears.',
        [action('Return to the write status', 'write-result')]],
      finish: ['Start XY Forever.', 'Switch off and wait 5 seconds. Hold PLAY while switching on, and release it when the logo appears.',
        deps.serial ? [action(busy ? 'Finishing…' : 'Connect and finish', null, 'serial-finish')] : [],
        ['The first start after copying can stay on the logo for up to 20 seconds: that is normal.', 'XY Forever’s menu then says "No firmware found": also expected.',
          'Select Connect and finish once the XY Forever menu shows. A page that connected before finds your OP–XY by itself; otherwise choose it in the browser’s list.',
          CHOOSE_TEXT, 'If your OP–XY asks to allow this computer, press PLAY.',
          ...(deps.serial ? [] : ['This browser has no Web Serial: open this page in Chrome or Edge to finish.'])]],
      'mtp-help': ['Start fresh with OS 1.2.1.', 'Format the drive, reinstall official OS 1.2.1, then continue installing XY Forever.',
        [action('I understand the data loss · Show format steps', 'format-drive', 'format-start')],
        ['Formatting erases all projects, samples, presets and soundpacks. Back up your files and check the copies.',
          'Can’t back up files you need? Stop here.', 'Download OS 1.2.1 below. Keep your soundpack installers for later.']],
      'format-drive': ['Format the drive on your OP–XY.', 'This erases your data. Keep the OP–XY on power.', [action('Format finished · Reinstall OS 1.2.1', 'format-upload')],
        ['In te-boot, press track 5: “format drive”.', 'Check that you see “format drive”, not “write os to boot partition”.',
          'Follow the device’s prompts. If the menu differs, stop.', 'Wait until formatting finishes before continuing.']],
      'format-upload': ['Reinstall official OS 1.2.1.', 'Use the official .tfw download below.', [action('1.2.1 reinstalled · Continue', null, 'recovery-retry')],
        ['Once formatting finishes, switch off. Hold COM while switching on.', 'Press track 1: “upload firmware”, then connect USB.',
          'Copy the .tfw file to the removable disk and eject it.', 'Wait for the update to finish before continuing.']],
      stop: [stop?.title ?? '', stop?.detail ?? '', [action('Start again', null, 'flow-restart')],
        stop?.kind === 'update' || stop?.kind === 'reinstall' ? official : []],
      done: ['XY Forever is installed.', finished ? 'Your OP–XY is starting OS 1.2.1 through XY Forever.'
        : 'Your OP–XY runs XY Forever, with OS 1.2.1 as the firmware it starts.', [],
        ['Switch on as usual: OS 1.2.1 starts.', 'Hold PLAY while switching on: XY Forever’s menu opens.',
          'Hold COM while switching on: te-boot, with the dev menu still on. It is your way back.']],
    };
    return stages;
  }
  const content = (): Stage => table()[stage]!;

  function click(a: Action): void {
    if (busy) return;
    switch (a.id) {
      case 'te-check': void act(check); return;
      case 'dev-enable': void act(enable); return;
      case 'mtp-send': void act(send); return;
      case 'download': void act(download); return;
      case 'serial-finish': void act(finish); return;
      case 'serial-find': void act(findChosen); return;
      case 'recovery-retry': case 'flow-restart':
        formatting = delivered = stockChecked = false; stop = null; go('boot', 0); return;
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
    stockChecked = delivered = true;
  }

  async function checkStock(file: Blob): Promise<void> {
    const verdict = await checkDroppedStock(file);
    busy = false;
    if (verdict.kind !== 'ok') halt(verdict); else advance('transfer');
  }

  async function download(): Promise<void> {
    teBoot?.close();
    downloadBlob(new Blob([(await ourImage()) as BlobPart]), 'os.ldr');
    say('Downloaded os.ldr. Now drag it to your OP–XY’s root folder.');
  }

  async function finish(): Promise<void> {
    say('Connecting to XY Forever…');
    await detecting;                 // it holds the port until it is done
    const client = await deps.connect();
    const off = client.onAsk((asking) => say(asking ? `${ASK_TEXT}: your OP–XY asks to allow this computer. Press PLAY to allow it.` : 'Allowed.'));
    try {
      await finishInstall(client, { release: release!, stockChecked, step: (text, f) => say(text, f ?? null) });
    } finally {
      off();
      await client.close().catch(() => {});
    }
    busy = false; finished = true; go('done', 0);
  }

  const link = (href: string, text: string): HTMLAnchorElement => h('a', { href, target: '_blank', rel: 'noopener noreferrer' }, text);

  function dropBox(): HTMLElement {
    const pick = h('input', { type: 'file', onchange: () => { const f = pick.files?.[0]; if (f) void act(() => checkStock(f)); } });
    return h('label', { class: 'drop', ondragover: (e: DragEvent) => e.preventDefault(),
      ondrop: (e: DragEvent) => { e.preventDefault(); const f = e.dataTransfer?.files[0]; if (f) void act(() => checkStock(f)); } },
    'Drop os.ldr here, or click to choose it', pick);
  }

  /** Open-XY's MTP troubleshooting FAQ, adapted to one file and to XY Forever's recovery (the dev menu stays on). */
  const faq = (): HTMLElement => h('details', { id: 'mtp-faq', open: stage === 'mtp-help' }, h('summary', {}, 'MTP troubleshooting FAQ'),
    h('h3', {}, 'Did installing soundpacks cause this?'),
    h('p', {}, 'We don’t know. Persistent failures were reported with soundpacks installed, but that does not establish a cause. Removing soundpacks has not been verified as a fix.'),
    h('h3', {}, 'The copy keeps failing. What next?'),
    h('p', {}, 'Close Field Kit, OpenMTP, Android File Transfer and any File Explorer windows showing your OP–XY, then try again. If it still fails, the fresh-install guide walks you through formatting the drive and reinstalling official OS 1.2.1 before installing XY Forever. Back up your files first: formatting erases the drive. This resolved a reported MTP problem, but is not a guaranteed fix.'),
    h('h3', {}, 'MTP works, but the original OS file is missing.'),
    h('p', {}, 'This is a separate problem: the page then asks you to reinstall official OS 1.2.1, without formatting the drive.'),
    h('h3', {}, 'Where does the file go?'),
    h('p', {}, 'Copy ', h('code', {}, 'os.ldr'), ' to the root, not into ', h('code', {}, 'upgrade/firmware/blackfin/bin'), '. After copying, follow the write-OS steps; copying alone does not install firmware.'));

  function render(): void {
    const [title, detail, actions, instructions = []] = content();
    root.dataset['stage'] = stage;
    root.setAttribute('aria-busy', String(busy));
    const official = stage === 'mtp-help' || stage === 'format-upload' || (stage === 'stop' && (stop?.kind === 'update' || stop?.kind === 'reinstall'));
    const missing = [!deps.midi && 'Web MIDI', !deps.serial && 'Web Serial'].filter(Boolean);
    const startable = stage !== 'boot' || (release !== null && deps.midi !== null);
    const parts: (Node | string | false | null)[] = [
      h('h1', {}, 'Install XY Forever'),
      stage === 'boot' && h('p', { class: 'lede' }, 'For an OP–XY with teenage engineering’s OS 1.2.1. You need this page in Chrome or Edge on a Mac or Windows computer, and a USB-C data cable: no other apps.'),
      !release && h('div', { class: 'card refusal', 'data-refusal': 'no-release' }, h('h2', {}, 'No XY Forever release here yet'),
        h('p', {}, 'This copy of the web app has no XY Forever release to install.')),
      stage === 'boot' && missing.length > 0 && h('div', { class: 'card refusal', 'data-refusal': 'browser' },
        h('p', {}, `This browser lacks ${missing.join(' and ')}. Open this page in Chrome or Edge on a Mac or Windows computer.`)),
      h('div', { class: stage === 'stop' ? 'card refusal' : 'card', 'data-verdict': stage === 'stop' ? stop?.kind : undefined },
        formatting && h('p', { class: 'muted' }, 'Start fresh with OS 1.2.1'),
        h('h2', { id: 'flow-title' }, title),
        detail && h('p', {}, detail),
        instructions.length > 0 && h('ol', { class: 'steps' }, ...instructions.map((t) => h('li', {}, t))),
        official && h('p', { 'data-official': '1.2.1' }, link(OFFICIAL_121_TFW, 'Download official OS 1.2.1 (.tfw)'), ' · ', link(OFFICIAL_UPDATE_GUIDE, 'Official installation guide'),
          h('br'), h('span', { class: 'warn-text' }, 'Use exactly this 1.2.1 file. Never install 1.2.5, the newest on teenage engineering’s site: it cannot be undone, and XY Forever cannot run on it.')),
        stage === 'stock' && dropBox(),
        stage === 'transfer' && !sendsDirectly() && h('aside', { class: 'install-reminder' }, h('h3', {}, 'Yes, copy to the ROOT folder.'),
          h('p', {}, 'Copy os.ldr to the top level of your OP–XY. Do not put it in ', h('code', {}, 'upgrade/firmware/blackfin/bin'), '. ', h('strong', {}, 'Replace the existing file if prompted.'))),
        ['transfer', 'exit-transfer', 'write-os'].includes(stage) && h('aside', { class: 'install-reminder' }, h('h3', {}, 'You are not done yet!'),
          h('p', {}, 'Copying the file does not finish installation. Continue through the next steps to install the OS before restarting.')),
        log.length > 0 && h('ol', { class: 'transfer-log', 'aria-label': 'Transfer log' }, ...log.map((t) => h('li', {}, t))),
        fraction !== null && h('progress', { value: fraction, max: 1 }),
        startable && h('div', { class: 'row' }, ...actions.map((a, i) =>
          h('button', { type: 'button', class: i === 0 ? 'primary' : '', id: a.id, disabled: busy, onclick: () => click(a) }, a.label))),
        ['start-transfer', 'close-field-kit', 'transfer', 'stock'].includes(stage) &&
          h('button', { type: 'button', class: 'mtp-help-button', disabled: busy, onclick: () => advance('mtp-help') }, 'I’m having trouble connecting to MTP'),
        ['mtp-help', 'transfer'].includes(stage) && faq(),
        status && h('p', { class: error ? 'install-status error' : 'install-status', role: 'status' }, status),
        ['boot', 'enable', 'menu-help'].includes(stage) && h('details', { id: 'connection-help' }, h('summary', {}, 'Connection help'),
          h('p', {}, 'Use a compatible desktop browser, such as Chrome or Edge. Close other MIDI apps. If this page cannot get MIDI permission, use the ',
            link('https://bagofwaffles.github.io/ferret/op-xy/', 'hosted Ferret tool'), ' to enable the dev menu, then check the menu here again.'),
          h('p', { class: 'attribution' }, link('https://github.com/bagofwaffles/ferret', 'Ferret by Bag of Waffles'), ' · MIT license')),
        deps.serial && release && !['finish', 'done'].includes(stage) && h('p', { class: 'muted' }, 'Already running XY Forever? ',
          h('button', { type: 'button', id: 'serial-find', disabled: busy, onclick: () => click(action('Find my OP–XY', null, 'serial-find')) }, 'Find my OP–XY')),
        depth > 0 && !NO_BACK.includes(stage) &&
          h('div', { class: 'flow-navigation' }, h('button', { type: 'button', disabled: busy, onclick: back }, '← Back'))),
    ];
    root.replaceChildren(...parts.filter((c): c is Node | string => c !== null && c !== false && c !== ''));
  }

  function back(): void { if (!busy && depth > 0) history.back(); }

  return { render, show };
}
