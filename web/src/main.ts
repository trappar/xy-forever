// The XY Forever web app: Connect, Device, Mods, Files. Plain DOM; one device action at a time.
// ?sim=PORT: the device simulator on 127.0.0.1:PORT through the dev server's relay (development and tests).
import { Client, JobError, applyMods, fileListSource, fileReason, grantedPorts, isXyfError, libraryBases, listPackages, localSet, pushPackages,
  removePackage, requestPort, webSerial, webSerialOpener } from 'xy-forever-client';
import type { ApplyRequest, BuiltEntry, DirEntry, HeldGeneration, JobStatus, LocalSet, Opener, PackageSource } from 'xy-forever-client';
import { webSocketOpener } from 'xy-forever-client/dev';
import { CONFIG_FILE, CONFIG_OLD, CONTENT_STATE, CONTENT_STATE_OLD, ROOT, WRITE_RULE, changeRefusal, configText, contentText, folderWritable, groupMods,
  human, joinPath, modeText, nameProblem, parentOf, parseConfig } from './model.ts';
import type { SavedConfig } from './model.ts';
import { confirmAction, h, notice, promptText } from './ui.ts';

const sim = new URLSearchParams(location.search).get('sim');
let client: Client | null = null;
let busy = false;
const page = (id: string): HTMLElement => document.getElementById(`page-${id}`)!;

// ---- Navigation and actions ---------------------------------------------------------------------------

const PAGES = ['connect', 'device', 'mods', 'files'] as const;
type Page = (typeof PAGES)[number];
const loaders: Record<Page, () => Promise<void>> = { connect: async () => renderConnect(), device: loadDevice, mods: loadMods, files: () => browse(cwd) };

function current(): Page { const p = location.hash.slice(1) as Page; return PAGES.includes(p) ? p : 'connect'; }

function route(): void {
  const p = client ? current() : 'connect';
  for (const id of PAGES) page(id).hidden = id !== p;
  for (const a of document.querySelectorAll<HTMLAnchorElement>('#nav a')) a.classList.toggle('active', a.dataset['page'] === p);
  if (client || p === 'connect') run(loaders[p]);
}
window.addEventListener('hashchange', route);

function setLink(): void {
  document.getElementById('link')!.classList.toggle('on', client !== null);
  document.getElementById('link-text')!.textContent = client ? `Connected${sim ? ' (simulator)' : ''}` : 'Not connected';
}

/** Runs one device action; errors are shown in plain words. A second action waits for the first. */
async function run(work: () => Promise<void>, label = ''): Promise<void> {
  if (busy) { notice('Busy: wait for the current action to finish.', 'busy'); return; }
  busy = true;
  notice(label, 'busy');
  if (!label) notice('');
  try {
    await work();
    notice('');
  } catch (error) {
    notice(describe(error), 'error');
    if (client && !client.connected) { client = null; setLink(); route(); }
  } finally {
    busy = false;
  }
}

function describe(error: unknown): string {
  if (error instanceof JobError) return `The build stopped: ${error.message}`;
  if (isXyfError(error, 'busy')) return 'The OP-XY is busy with a build. Wait for it to finish, then try again.';
  if (isXyfError(error)) return error.fields && 'error' in error.fields ? fileReason(error) : error.message;
  return error instanceof Error ? error.message : String(error);
}

const btn = (label: string, onclick: () => void, cls = ''): HTMLButtonElement => h('button', { type: 'button', class: cls, onclick }, label);

// ---- Connect -----------------------------------------------------------------------------------------

function renderConnect(): void {
  const supported = sim !== null || webSerial() !== null;
  page('connect').replaceChildren(
    h('h1', {}, 'Connect your OP-XY'),
    h('p', { class: 'lede' }, 'Start the OP-XY into the XY Forever menu, connect it by USB, then press Connect and choose XY Forever in the list.'),
    h('div', { class: 'card' },
      supported
        ? h('div', { class: 'row' },
          client ? h('span', {}, `Connected to XY Forever ${client.info.version ?? '?'}.`) : btn('Connect', () => void run(connect, 'Looking for the OP-XY...'), 'primary'),
          client && btn('Disconnect', () => void run(disconnect)),
          h('span', { class: 'muted' }, sim ? `Simulator on 127.0.0.1:${sim} (development)` : 'Web Serial over USB'))
        : h('div', {}, h('h2', {}, 'This browser cannot talk to the OP-XY'),
          h('p', { class: 'muted' }, 'XY Forever needs Web Serial, which only desktop Chrome (and Edge) have. Open this page in Chrome on a computer. Safari, Firefox and phones are not supported.'))),
  );
}

async function opener(): Promise<Opener> {
  if (sim) return webSocketOpener(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/xyf-relay?sim=${encodeURIComponent(sim)}`);
  if ((await grantedPorts()).length === 0) await requestPort();
  return webSerialOpener();
}

async function connect(): Promise<void> {
  client = await Client.connect(await opener(), { timeoutMs: 10_000 });
  setLink();
  location.hash = '#device';
  busy = false;
  route();
}

async function disconnect(): Promise<void> {
  const c = client;
  client = null;
  setLink();
  await c?.close();
  renderConnect();
}

// ---- Device ------------------------------------------------------------------------------------------

async function readText(path: string): Promise<string | null> {
  try { return new TextDecoder().decode(await client!.readFile(path)); } catch (e) { if (isXyfError(e, 'not_found')) return null; throw e; }
}

/** The saved configuration, null: none (no mods); undefined: could not be read. */
async function savedConfig(): Promise<SavedConfig | null | undefined> {
  try {
    const text = await readText(CONFIG_FILE) ?? await readText(CONFIG_OLD);
    return text === null ? null : parseConfig(text);
  } catch { return undefined; }
}

async function loadDevice(): Promise<void> {
  const c = client!;
  const info = await c.status();
  const state = await c.readState();
  let boots = 'No default set: the menu decides at power-on.';
  if (state?.default) {
    const entry = await c.libraryEntry(state.default).catch(() => ({} as Record<string, string>));
    boots = `${entry['name'] ?? state.default}${entry['version'] ? ` ${entry['version']}` : ''} (library entry ${state.default})`;
  }
  const config = await savedConfig();
  const content = await readText(CONTENT_STATE);
  const old = content === null && await readText(CONTENT_STATE_OLD) !== null;
  page('device').replaceChildren(
    h('h1', {}, 'Device'), h('p', { class: 'lede' }, 'What the OP-XY reports right now.'),
    h('div', { class: 'card' }, h('dl', { class: 'facts', id: 'device-facts' },
      h('dt', {}, 'XY Forever'), h('dd', { 'data-fact': 'version' }, info.version ?? 'unknown'),
      h('dt', {}, 'Image'), h('dd', { 'data-fact': 'image' }, info.image ?? 'unknown'),
      h('dt', {}, 'Running'), h('dd', {}, modeText(info.mode)),
      h('dt', {}, 'Boots next'), h('dd', { 'data-fact': 'boots' }, boots),
      h('dt', {}, 'Saved mods'), h('dd', {}, config === undefined ? 'Could not read the saved configuration.' : configText(config)),
      h('dt', {}, 'Content'), h('dd', {}, contentText(content, old)),
      h('dt', {}, 'Protocol'), h('dd', { class: 'muted' }, `${info.proto}${info.dev ? ', developer build' : ''}`))),
    h('div', { class: 'row' }, btn('Refresh', () => void run(loadDevice))),
  );
}

// ---- Mods --------------------------------------------------------------------------------------------

let chosen: { source: PackageSource; set: LocalSet } | null = null;
let held: HeldGeneration[] = [];
let building: AbortController | null = null;

/**
 * How "Build on device" runs. Today: the low-level mods.apply job (applyMods), which builds the
 * library entry mods-a or mods-b with its content not installed, and changes nothing that boots
 * (makeDefault, which installs content and changes the default, is deliberately not offered yet). Seam: when the device exposes its
 * Save over USB (build + install content + save config, as the menu's Save does), replace this.
 */
export let buildOnDevice = (c: Client, request: ApplyRequest, onProgress: (s: JobStatus) => void, signal: AbortSignal): Promise<BuiltEntry> =>
  applyMods(c, request, { onProgress, signal });
export function setBuildOnDevice(f: typeof buildOnDevice): void { buildOnDevice = f; }

async function loadMods(): Promise<void> {
  const c = client!;
  held = await listPackages(c);
  const config = await savedConfig();
  const root = page('mods');
  const picker = h('input', { type: 'file', webkitdirectory: true, multiple: true, onchange: () => void pick(picker) });
  root.replaceChildren(
    h('h1', {}, 'Mods'),
    h('p', { class: 'lede' }, 'Mod packages stored on the OP-XY. Turning mods on or off and saving is done in the device\'s Mods menu; this page adds and removes packages.'),
    h('div', { class: 'card' },
      h('div', { class: 'row' }, h('h2', { style: 'margin:0' }, 'Add packages'), h('span', { class: 'spacer' }),
        h('label', { class: 'button' }, 'Choose package folder...', picker),
        btn('Send to OP-XY', () => void run(send, 'Sending packages...'), 'primary')),
      h('p', { class: 'muted', id: 'mods-chosen' }, chosen ? chosenText(chosen.set) : 'Choose a generation folder (from modkit split): it holds base/ and one folder per mod.'),
      h('progress', { id: 'mods-progress', value: 0, max: 1, hidden: true })),
    ...(held.length === 0 ? [h('div', { class: 'card muted' }, 'No mod packages on this OP-XY yet.')] : held.map((g) => generationCard(g, config))),
    buildCard(config),
  );
}

function chosenText(set: LocalSet): string {
  const files = [...set.packages.values()].flat();
  return `Chosen: ${set.profile} ${set.generation}, ${set.packages.size} packages, ${human(files.reduce((n, f) => n + f.data.length, 0))}.`;
}

function generationCard(g: HeldGeneration, config: SavedConfig | null | undefined): HTMLElement {
  if (g.older || g.newer) {                          // no store.txt, or a newer store version (#124)
    const kind = g.older ? 'older' : 'newer';
    return h('div', { class: 'card' },
      h('div', { class: 'row' }, h('h2', { style: 'margin:0' }, `${g.generation} (${kind} layout)`), h('span', { class: 'spacer' }),
        btn('Remove all', () => void run(() => remove(g.generation, null, `the ${kind} ${g.generation} folder`)), 'small danger')),
      h('p', { class: 'muted' }, g.older
        ? 'Packages sent by an older XY Forever, in folders too deep for the dev menu\'s file transfer. Remove them, then send the package set again.'
        : 'Packages in a newer layout than this page reads: update it, or remove them and send the package set again.'));
  }
  const sameSet = config && config.profile === g.profile && config.generation === g.generation;
  const groups = groupMods(g.packages, sameSet || config === undefined ? config : null);
  const body = h('tbody');
  for (const group of groups) {
    body.append(h('tr', {}, h('td', { class: 'group-title', colspan: 4 }, group.label)));
    for (const r of group.rows) {
      body.append(h('tr', { 'data-mod': r.folder },
        h('td', {}, h('b', {}, r.name), ' ', h('span', { class: 'muted mono' }, r.version)),
        h('td', {}, r.on === null ? h('span', { class: 'tag' }, 'unknown') : h('span', { class: r.on ? 'tag on' : 'tag' }, r.on ? 'on' : 'off'),
          ' ', !r.complete && h('span', { class: 'tag warn', title: 'Sending was interrupted: send it again.' }, 'incomplete')),
        h('td', { class: 'muted hide-narrow' }, human(r.bytes)),
        h('td', { class: 'actions' }, btn('Remove', () => void run(() => remove(r.folder, g.generation, r.name)), 'small danger'))));
    }
  }
  const baseHeld = g.packages.some((p) => p.name === 'base');
  return h('div', { class: 'card' },
    h('div', { class: 'row' }, h('h2', { style: 'margin:0' }, `${g.profile} ${g.generation}`), !baseHeld && h('span', { class: 'tag warn' }, 'no base package'),
      h('span', { class: 'spacer' }), btn('Remove all', () => void run(() => remove(g.generation, null, `the whole ${g.generation} package set`)), 'small danger')),
    h('p', { class: 'muted' }, config === undefined ? 'On/off: the saved configuration could not be read.' : sameSet ? 'On/off: as saved on the device.' : 'On/off: this set is not the saved one, so all are off.'),
    h('table', {}, body));
}

async function pick(input: HTMLInputElement): Promise<void> {
  const source = fileListSource([...input.files ?? []]);
  input.value = '';
  chosen = null;
  await run(async () => {
    chosen = { source, set: await localSet(source) };
    document.getElementById('mods-chosen')!.textContent = chosenText(chosen.set);
  });
}

async function send(): Promise<void> {
  if (!chosen) throw new Error('Choose a package folder first.');
  const bar = document.getElementById('mods-progress') as HTMLProgressElement;
  bar.hidden = false;
  const summary = await pushPackages(client!, chosen.source, { onProgress: (done, total) => { bar.max = total || 1; bar.value = done; } });
  chosen = null;
  await loadMods();
  notice(`Sent ${summary.sent} package${summary.sent === 1 ? '' : 's'} (${human(summary.bytes)}); ${summary.skipped} already there.`);
}

async function remove(what: string, generation: string | null, label: string): Promise<void> {
  const opts = generation ? { generation } : {};
  const plan = await removePackage(client!, what, { ...opts, dryRun: true });
  const files = plan.reduce((n, r) => n + r.files, 0);
  if (!await confirmAction(`Remove ${label}?`, `${files} files (${human(plan.reduce((n, r) => n + r.bytes, 0))}) are deleted from the OP-XY. A saved build that uses it keeps working until you change mods.`, 'Remove', true)) return;
  await removePackage(client!, what, opts);
  await loadMods();
}

function buildCard(config: SavedConfig | null | undefined): HTMLElement {
  const card = h('div', { class: 'card lowlevel', id: 'build' });
  const gens = held.filter((g) => g.packages.some((p) => p.name === 'base' && p.complete));
  card.append(h('h2', {}, 'Build on device'),
    h('p', { class: 'muted' }, 'The OP-XY assembles an Open-XY firmware from the packages it holds into a spare library entry (mods-a or mods-b, replacing the older one). This does not change what boots and does not install content: use Save in the device\'s Mods menu for that. It stays here until the device offers Save over USB.'));
  if (gens.length === 0) { card.append(h('p', {}, 'Send a package set with its base first.')); return card; }
  const gen = h('select', { 'aria-label': 'Package set' }, ...gens.map((g, i) => h('option', { value: String(i) }, `${g.profile} ${g.generation}`)));
  const base = h('select', { 'aria-label': 'Base firmware' });
  const name = h('input', { type: 'text', value: 'Open-XY', 'aria-label': 'Name', maxlength: 47 });
  const checks = h('div', { class: 'checks' });
  const status = h('p', { class: 'muted' });
  const bar = h('progress', { value: 0, max: 1, hidden: true });
  const fill = (): void => {
    const g = gens[Number(gen.value)]!;
    checks.replaceChildren(...groupMods(g.packages.filter((p) => p.complete), config).flatMap((grp) => grp.rows).map((r) =>
      h('label', {}, h('input', { type: 'checkbox', value: r.id, checked: r.on === true, 'data-extension': r.extension ? '1' : '' }), `${r.name}`, h('span', { class: 'muted' }, r.kind))));
  };
  gen.addEventListener('change', fill);
  fill();
  void libraryBases(client!).then((list) => base.replaceChildren(...list.map((b) => h('option', { value: b.id }, `${b.name} (${b.id})`)))).catch(() => undefined);
  const cancel = btn('Cancel', () => building?.abort(), 'danger');
  cancel.disabled = true;
  const start = btn('Build on device', () => void run(async () => {
    const g = gens[Number(gen.value)]!;
    const on = [...checks.querySelectorAll<HTMLInputElement>('input:checked')];
    const request: ApplyRequest = { base: base.value, profile: g.profile, generation: g.generation, name: name.value.trim(),
      machines: on.filter((i) => !i.dataset['extension']).map((i) => i.value), extensions: on.filter((i) => i.dataset['extension']).map((i) => i.value) };
    if (!request.base) throw new Error('Choose a base firmware from the library.');
    if (request.machines.length === 0) throw new Error('Turn on at least one machine.');
    if (!await confirmAction('Build on the OP-XY?', `The OP-XY builds "${request.name}" from ${request.machines.length + (request.extensions?.length ?? 0)} mods on ${base.selectedOptions[0]?.textContent ?? request.base}. It replaces the older of the two mod library entries, takes a few minutes, and the device is busy meanwhile. What boots does not change.`, 'Build')) return;
    building = new AbortController();
    cancel.disabled = false; bar.hidden = false;
    try {
      const done = await buildOnDevice(client!, request, (s) => { bar.max = s.total || 1; bar.value = s.done; status.textContent = `${s.phase}: ${s.done} of ${s.total}`; }, building.signal);
      status.textContent = `Built in ${(done.status.elapsedMs / 1000).toFixed(1)} s into library entry ${done.entry} (content not installed: it does not boot as it is).`;
    } finally { building = null; cancel.disabled = true; }
  }, 'Building on the OP-XY...'), 'primary');
  card.append(h('div', { class: 'row' }, gen, base, name), checks, h('div', { class: 'row' }, start, cancel), bar, status);
  return card;
}

// ---- Files -------------------------------------------------------------------------------------------

let cwd = ROOT;

async function browse(path: string): Promise<void> {
  const entries = await client!.listDir(path);
  cwd = path.replace(/\/+$/, '') || ROOT;
  entries.sort((a, b) => Number(a.kind !== 'folder') - Number(b.kind !== 'folder') || a.name.localeCompare(b.name));
  const writable = folderWritable(cwd);
  const parts = cwd.split('/').filter(Boolean);
  const crumbs = h('div', { class: 'crumbs', id: 'crumbs' }, ...parts.flatMap((p, i) => {
    const here = '/' + parts.slice(0, i + 1).join('/');
    return [i > 0 ? '/' : '', i === parts.length - 1 ? h('b', {}, p) : btn(p, () => void run(() => browse(here)))];
  }));
  const upload = h('input', { type: 'file', multiple: true, onchange: () => void run(() => uploadFiles(upload), 'Uploading...') });
  const body = h('tbody', { id: 'files-body' });
  if (cwd !== ROOT) body.append(h('tr', {}, h('td', { colspan: 3 }, h('button', { type: 'button', class: 'linkish', onclick: () => void run(() => browse(parentOf(cwd))) }, '.. (up)'))));
  for (const e of entries) body.append(fileRow(e, writable));
  if (entries.length === 0) body.append(h('tr', {}, h('td', { colspan: 3, class: 'muted' }, 'Empty folder.')));
  const progress = h('progress', { id: 'files-progress', value: 0, max: 1, hidden: true });
  page('files').replaceChildren(
    h('h1', {}, 'Files'),
    h('p', { class: 'lede' }, 'Browse the OP-XY\'s storage. Everything can be downloaded.'),
    h('p', { class: 'rules', id: 'files-rules' }, `Changes are allowed ${WRITE_RULE}. `, writable ? 'This folder accepts changes.' : 'This folder is read-only.'),
    h('div', { class: 'card' },
      h('div', { class: 'row' }, crumbs, h('span', { class: 'spacer' }),
        writable && h('label', { class: 'button' }, 'Upload...', upload),
        writable && btn('New folder', () => void run(newFolder))),
      progress,
      h('table', {}, h('thead', {}, h('tr', {}, h('th', {}, 'Name'), h('th', { class: 'hide-narrow' }, 'Size'), h('th', {}))), body)),
  );
}

function fileRow(e: DirEntry, writable: boolean): HTMLElement {
  const path = joinPath(cwd, e.name);
  const name = e.kind === 'folder'
    ? h('button', { type: 'button', class: 'linkish', onclick: () => void run(() => browse(path)) }, `${e.name}/`)
    : h('span', {}, e.name);
  return h('tr', { 'data-name': e.name },
    h('td', {}, name),
    h('td', { class: 'muted hide-narrow' }, e.kind === 'file' ? human(e.size) : ''),
    h('td', { class: 'actions' },
      e.kind === 'file' && btn('Download', () => void run(() => download(path, e.name), 'Downloading...'), 'small'),
      e.kind !== 'other' && btn('Copy', () => void run(() => copy(path, e)), 'small'),
      writable && e.kind !== 'other' && btn('Rename/Move', () => void run(() => move(path)), 'small'),
      writable && e.kind !== 'other' && btn('Delete', () => void run(() => removePath(path, e)), 'small danger')));
}

function bar(): HTMLProgressElement { const b = document.getElementById('files-progress') as HTMLProgressElement; b.hidden = false; return b; }

async function download(path: string, name: string): Promise<void> {
  const b = bar();
  const data = await client!.readFile(path, { onProgress: (done) => { b.removeAttribute('value'); b.title = human(done); } });
  const url = URL.createObjectURL(new Blob([data as BlobPart]));
  h('a', { href: url, download: name }).click();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
  b.hidden = true;
}

async function uploadFiles(input: HTMLInputElement): Promise<void> {
  const files = [...input.files ?? []];
  input.value = '';
  const existing = new Set((await client!.listDir(cwd)).map((e) => e.name));
  const replaced = files.filter((f) => existing.has(f.name)).map((f) => f.name);
  if (replaced.length && !await confirmAction('Replace files?', `These are replaced on the OP-XY:\n${replaced.join('\n')}`, 'Replace', true)) return;
  const b = bar();
  for (const f of files) {
    const target = joinPath(cwd, f.name);
    const why = changeRefusal(target);
    if (why) throw new Error(why);
    await client!.writeFile(target, new Uint8Array(await f.arrayBuffer()), { onProgress: (done, total) => { b.max = total ?? 1; b.value = done; } });
  }
  await browse(cwd);
}

async function newFolder(): Promise<void> {
  const name = await promptText('New folder', `In ${cwd}`, '', 'Create');
  if (name === null) return;
  const problem = nameProblem(name) ?? changeRefusal(joinPath(cwd, name), true);
  if (problem) throw new Error(problem);
  await client!.makeDir(joinPath(cwd, name));
  await browse(cwd);
}

async function move(path: string): Promise<void> {
  const target = await promptText('Rename or move', `Type the new full path for ${path}. Nothing is replaced.`, path, 'Rename');
  if (target === null || target === path) return;
  const why = changeRefusal(target);
  if (why) throw new Error(why);
  await client!.renamePath(path, target);
  await browse(cwd);
}

async function copy(path: string, e: DirEntry): Promise<void> {
  const suggestion = folderWritable(cwd) ? `${path}-copy` : joinPath('/yaffs2/content', e.name);
  const target = await promptText(`Copy ${e.kind}`, `Type the full path of the copy. It must be ${WRITE_RULE}.`, suggestion, 'Copy');
  if (target === null) return;
  const why = changeRefusal(target);
  if (why) throw new Error(why);
  await client!.copyTree(path, target);
  await browse(cwd);
}

async function removePath(path: string, e: DirEntry): Promise<void> {
  if (e.kind === 'folder') {
    const inside = await client!.listDir(path);
    if (inside.length) throw new Error(`${e.name} is not empty: delete what it holds first.`);
  }
  if (!await confirmAction(`Delete ${e.name}?`, `${path} is deleted from the OP-XY. This cannot be undone.`, 'Delete', true)) return;
  if (e.kind === 'folder') await client!.removeDir(path); else await client!.deleteFile(path);
  await browse(cwd);
}

// ---- Start -------------------------------------------------------------------------------------------

setLink();
route();
(window as Window & { xyfApp?: unknown }).xyfApp = { setBuildOnDevice };
