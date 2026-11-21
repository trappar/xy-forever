// The demo page (index.html, npm run dev): a test bed for the client, plain DOM, public entry only
// (plus ./dev for the WebSocket transport). The Files panel browses from /yaffs2: download, upload
// (picker or drop), delete, new folder, rename/move, copy (a folder: copyTree); changes go through
// confirm()/prompt(), and a hint names the folders that accept them. The Mods panel (src/mods.ts):
// what the device holds, send a chosen package folder (a generation folder), remove a package; and Build on
// device: choose a generation's machines and extensions, a base entry and a name, and the OP-XY assembles
// the firmware itself into library entry mods-a or mods-b, content not installed (applyMods; Cancel stops it;
// every other button waits while it runs); Make default (makeDefault) installs its content after a confirmation. ?sim=PORT: the simulator on 127.0.0.1:PORT through the
// dev server's relay (xyfRelay in test/relay.ts); otherwise Web Serial, the chooser showing only
// XY Forever's service (USB_FILTERS: the product build's composite or the stock serial identity).
import { webSocketOpener } from '../src/dev.ts';
import { Client, JobError, PROTECTED_NAMES, WRITE_ROOTS, applyMods, decodeScreen, fileListSource, grantedPorts, isXyfError, libraryBases, listPackages, localSet,
  inWriteArea, makeDefault, pushPackages, readModsConfig, removePackage, requestPort, webSerialOpener } from '../src/index.ts';
import type { DirEntry, EntryKind, HeldGeneration, JobStatus, LocalSet, Opener, PackageSource } from '../src/index.ts';

const el = <T extends HTMLElement = HTMLElement>(id: string): T => document.getElementById(id) as T;
const query = new URLSearchParams(location.search);
const sim = query.get('sim');
const hex = (b: Uint8Array): string => [...b].map((x) => x.toString(16).padStart(2, '0')).join(' ');
let client: Client | null = null;
/** The package folder chosen for the Mods panel's Send. */
let chosen: { source: PackageSource; set: LocalSet } | null = null;
/** What the device holds (the Mods list), for Build on device's choices. */
let held: HeldGeneration[] = [];
/** The running build's cancel (Build on device), else null. */
let building: AbortController | null = null;
/** The library entry the last build became (Make default's target), else null. */
let built: string | null = null;

el('via').textContent = sim ? `Simulator on 127.0.0.1:${sim} (relay)` : 'OP-XY over Web Serial';

function show(status: string): void {
  el('status').textContent = status;
  const idle = status !== 'working';
  el<HTMLButtonElement>('connect').disabled = !idle || client !== null;
  for (const id of ['disconnect', 'read', 'screenshot']) el<HTMLButtonElement>(id).disabled = !idle || client === null;
  for (const input of document.querySelectorAll<HTMLButtonElement | HTMLInputElement | HTMLSelectElement>(
    '#files button, #files input, #mods button, #mods input, #mods select')) {
    input.disabled = !idle || client === null;
  }
  el<HTMLButtonElement>('mods-send').disabled ||= chosen === null;
  el<HTMLButtonElement>('build-cancel').disabled = building === null || building.signal.aborted;
  el<HTMLButtonElement>('build-default').disabled ||= built === null;
  if (client === null) {
    built = null;
    el('crumbs').textContent = ''; el('files-hint').textContent = ''; el('files-list').querySelector('tbody')!.textContent = '';
    el('mods-list').querySelector('tbody')!.textContent = '';
    el('config-saved').textContent = '-'; el('config-content').textContent = '-';
    held = [];
    el('build-generation').textContent = ''; el('build-base').textContent = ''; el('build-choices').querySelector('tbody')!.textContent = '';
  }
}

/** Runs one action from a button; an error is shown with its typed code. */
function action(work: () => Promise<void>): () => void {
  return () => {
    el('error').textContent = '';
    show('working');
    work().catch((error: unknown) => {
      el('error').textContent = isXyfError(error) ? `${error.code}: ${error.message}` : `error: ${String(error)}`;
      if (client && !client.connected) client = null;
    }).finally(() => show(client ? 'connected' : 'disconnected'));
  };
}

/** The link to open; null when the person closed Chrome's port chooser without choosing (NotFoundError). */
async function opener(): Promise<Opener | null> {
  if (sim) return webSocketOpener(`${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/xyf-relay?sim=${encodeURIComponent(sim)}`);
  if ((await grantedPorts()).length === 0) {
    try {
      await requestPort();                                        // the chooser, from this click
    } catch (error) {
      if (error instanceof DOMException && error.name === 'NotFoundError') return null;
      throw error;
    }
  }
  return webSerialOpener();
}

el('connect').addEventListener('click', action(async () => {
  const open = await opener();
  if (open === null) {
    el('error').textContent = 'no port chosen';
    return;
  }
  client = await Client.connect(open, { timeoutMs: 10_000 });
  if (query.has('unattended') && client.info.ask) {
    await client.close();
    client = null;
    el('error').textContent = 'NEED: turn off Ask before USB control in the XY Forever settings for an unattended test.';
    return;
  }
  const { version, image, proto, mode } = client.info;
  el('version').textContent = `${version ?? '?'}, image ${image ?? '?'}`;
  el('proto').textContent = proto;
  el('mode').textContent = mode ?? '?';
  await browse('/yaffs2');
  await showMods();
}));

el('disconnect').addEventListener('click', action(async () => {
  const closing = client;
  client = null;
  await closing?.close();
}));

el('read').addEventListener('click', action(async () => {
  const data = await client!.readFile(el<HTMLInputElement>('path').value);
  el('size').textContent = `${data.length} bytes`;
  el('bytes').textContent = hex(data.subarray(0, 64)) + (data.length > 64 ? ' ...' : '');
}));

el('screenshot').addEventListener('click', action(async () => {
  const { width, height, rgba } = decodeScreen(await client!.screenshot());
  const canvas = el<HTMLCanvasElement>('screen');
  canvas.width = width;
  canvas.height = height;
  canvas.getContext('2d')!.putImageData(new ImageData(new Uint8ClampedArray(rgba), width, height), 0, 0);
}));

// ---- Files ----------------------------------------------------------------------------------------

let cwd = '/yaffs2';
const inside = (dir: string, name: string): string => `${dir.replace(/\/+$/, '')}/${name}`;
const human = (n: number): string => n < 1024 ? `${n} B` : n < 1 << 20 ? `${(n / 1024).toFixed(1)} KiB` : `${(n / (1 << 20)).toFixed(1)} MiB`;
const progress = (text: string): void => { el('files-progress').textContent = text; };

/** What a row offers, by kind. */
const ROW_ACTIONS: readonly { label: string; kinds: readonly EntryKind[]; run(path: string, entry: DirEntry): Promise<void> }[] = [
  { label: 'Download', kinds: ['file'], run: download },
  { label: 'Rename/Move', kinds: ['file', 'folder'], run: move },
  { label: 'Copy', kinds: ['file', 'folder'], run: copy },
  { label: 'Delete', kinds: ['file', 'folder'], run: remove },
];

function button(label: string, onClick: () => void, className = ''): HTMLButtonElement {
  const b = document.createElement('button');
  b.textContent = label;
  b.className = className;
  b.addEventListener('click', onClick);
  return b;
}

/** Lists path (folders first, then by name) and makes it the current folder. */
async function browse(path: string): Promise<void> {
  const entries = await client!.listDir(path);
  cwd = path.replace(/\/+$/, '') || '/yaffs2';
  const folder = (e: DirEntry): number => e.kind === 'folder' ? 0 : 1;
  entries.sort((a, b) => folder(a) - folder(b) || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));

  const crumbs = el('crumbs');
  crumbs.textContent = '';
  const parts = cwd.split('/').filter(Boolean);                     // ['yaffs2', ...]
  parts.forEach((part, i) => {
    if (i > 0) crumbs.append(' / ');
    const here = '/' + parts.slice(0, i + 1).join('/');
    if (i === parts.length - 1) crumbs.append(Object.assign(document.createElement('b'), { textContent: part }));
    else crumbs.append(button(part, action(() => browse(here))));
  });

  const writable = inWriteArea(`${cwd}/x`);
  el('files-hint').textContent = `Changes under ${WRITE_ROOTS.join(' and ')}, never ${PROTECTED_NAMES.join(' or ')}` +
    `${writable ? '' : ': this folder is read-only'}.`;

  const body = el('files-list').querySelector('tbody')!;
  body.textContent = '';
  for (const entry of entries) {
    const path = inside(cwd, entry.name);
    const row = body.insertRow();
    Object.assign(row.dataset, { name: entry.name, kind: entry.kind, size: String(entry.size) });
    const name = row.insertCell();
    if (entry.kind === 'folder') name.append(button(`${entry.name}/`, action(() => browse(path)), 'open'));
    else name.textContent = entry.name;
    Object.assign(row.insertCell(), { className: 'size', textContent: entry.kind === 'file' ? human(entry.size) : '-', title: `${entry.size} bytes` });
    const actions = Object.assign(row.insertCell(), { className: 'actions' });
    for (const a of ROW_ACTIONS) if (a.kinds.includes(entry.kind)) actions.append(button(a.label, action(() => a.run(path, entry))));
  }
}

async function download(path: string, entry: DirEntry): Promise<void> {
  const data = await client!.readFile(path, { onProgress: (done) => progress(`Downloading ${entry.name}: ${human(done)} of ${human(entry.size)}`) });
  progress(`Downloaded ${entry.name} (${human(data.length)})`);
  const saved = el('files-saved');
  const old = saved.querySelector('a');
  if (old) URL.revokeObjectURL(old.href);
  const link = Object.assign(document.createElement('a'), {
    href: URL.createObjectURL(new Blob([data.slice()])), download: entry.name, textContent: `Save ${entry.name} again`,
  });
  saved.replaceChildren(link);
  link.click();
}

async function remove(path: string, entry: DirEntry): Promise<void> {
  const what = entry.kind === 'folder' ? 'the empty folder' : 'the file';
  if (!confirm(`Delete ${what} ${path} from the OP-XY? This cannot be undone.`)) return;
  try {
    if (entry.kind === 'folder') await client!.removeDir(path);
    else await client!.deleteFile(path);
    progress(`Deleted ${path}`);
  } finally {
    await browse(cwd).catch(() => undefined);
  }
}

/** Rename/Move: the new path, prompted (pre-filled with the current one); the target must not exist. */
async function move(path: string): Promise<void> {
  const target = prompt(`Rename or move ${path} to (the new path must not exist):`, path);
  if (!target || target === path) return;
  try {
    await client!.renamePath(path, target);
    progress(`Moved ${path} to ${target}`);
  } finally {
    await browse(cwd).catch(() => undefined);
  }
}

/** Copy on the device: a file with copyFile, a folder and everything in it with copyTree. */
async function copy(path: string, entry: DirEntry): Promise<void> {
  const target = prompt(`Copy ${path}${entry.kind === 'folder' ? ' and everything in it' : ''} to (the new path must not exist):`, path);
  if (!target || target === path) return;
  try {
    if (entry.kind === 'folder') {
      const { files, bytes } = await client!.copyTree(path, target, { onProgress: (done) => progress(`Copying ${path}: ${human(done)}`) });
      progress(`Copied ${path} to ${target} (${files} files, ${human(bytes)})`);
    } else {
      const { bytes } = await client!.copyFile(path, target, { size: entry.size });
      progress(`Copied ${path} to ${target} (${human(bytes)})`);
    }
  } finally {
    await browse(cwd).catch(() => undefined);
  }
}

async function upload(files: readonly File[]): Promise<void> {
  if (files.length === 0) return;
  const list = files.map((f) => `${f.name} (${human(f.size)})`).join(', ');
  if (!confirm(`Write ${list} to ${cwd}/ on the OP-XY? A file with the same name is replaced.`)) return;
  try {
    for (const file of files) {
      const data = new Uint8Array(await file.arrayBuffer());
      await client!.writeFile(inside(cwd, file.name), data,
        { onProgress: (done, total) => progress(`Uploading ${file.name}: ${human(done)} of ${human(total ?? data.length)}`) });
      progress(`Uploaded ${file.name} (${human(data.length)})`);
    }
  } finally {
    await browse(cwd).catch(() => undefined);
  }
}

el('files-refresh').addEventListener('click', action(() => browse(cwd)));

el('files-mkdir').addEventListener('click', action(async () => {
  const name = prompt(`New folder in ${cwd}/:`);
  if (!name) return;
  await client!.makeDir(inside(cwd, name));
  await browse(cwd);
}));

el<HTMLInputElement>('files-upload').addEventListener('change', () => {
  const input = el<HTMLInputElement>('files-upload');
  const files = [...input.files ?? []];
  input.value = '';                                                 // the same file can be picked again
  action(() => upload(files))();
});

const drop = el('files-drop');
const canDrop = (): boolean => client !== null && el('status').textContent !== 'working';
drop.addEventListener('dragover', (e) => { e.preventDefault(); drop.classList.toggle('over', canDrop()); });
drop.addEventListener('dragleave', () => drop.classList.remove('over'));
drop.addEventListener('drop', (e) => {
  e.preventDefault();
  drop.classList.remove('over');
  if (canDrop()) action(() => upload([...e.dataTransfer?.files ?? []]))();
});

// ---- Mods -----------------------------------------------------------------------------------------

const modsProgress = (text: string): void => { el('mods-progress').textContent = text; };

/** What the device holds: a row per generation, then one per package (an interrupted one flagged), each with Remove. */
async function showMods(): Promise<void> {
  held = await listPackages(client!);
  await showConfig();
  await showBuild();
  const body = el('mods-list').querySelector('tbody')!;
  body.textContent = '';
  if (held.length === 0) {
    Object.assign(body.insertRow().insertCell(), { colSpan: 4, textContent: 'No mod packages on the device.' });
    return;
  }
  for (const { profile, generation, packages } of held) {
    const head = body.insertRow();
    head.className = 'generation';
    const files = packages.reduce((n, p) => n + p.files, 0), bytes = packages.reduce((n, p) => n + p.bytes, 0);
    Object.assign(head.insertCell(), { colSpan: 4, textContent: `${profile}/${generation}: ${packages.length} packages, ${files} files, ${human(bytes)}` });
    for (const p of packages) {
      const row = body.insertRow();
      Object.assign(row.dataset, { name: p.name, generation, complete: String(p.complete) });
      row.insertCell().textContent = p.name;
      const what = row.insertCell();
      what.textContent = [p.info['type'] ?? '?', p.info['machine'] ?? '', p.info['version'] ?? ''].filter(Boolean).join(' ');
      if (!p.complete) what.append(Object.assign(document.createElement('span'), { className: 'incomplete', textContent: ' incomplete: send again' }));
      Object.assign(row.insertCell(), { className: 'size', textContent: `${p.files} files, ${human(p.bytes)}`, title: `${p.bytes} bytes` });
      row.insertCell().append(button('Remove', action(() => removeMod(profile, generation, p.name))));
      row.cells[3]!.className = 'actions';
    }
  }
}

async function removeMod(profile: string, generation: string, name: string): Promise<void> {
  const [what] = await removePackage(client!, name, { generation, dryRun: true });
  if (!what) return;
  if (!confirm(`Remove the package ${name} (${what.files} files, ${human(what.bytes)}) of ${profile}/${generation} from the OP-XY?`)) return;
  try {
    await removePackage(client!, name, { generation });
    modsProgress(`Removed ${name} of ${profile}/${generation}`);
  } finally {
    await showMods().catch(() => undefined);
  }
}

el('mods-refresh').addEventListener('click', action(showMods));

// ---- Saved configuration --------------------------------------------------------------------------

/** Saved configuration fields (target/xyf_config.hpp: base, profile, generation, unverified, built, slot, stamp, mods). */
export type SavedConfig = Readonly<Record<string, string>>;

/** key=value lines (LF or CRLF; # comments; the last of a duplicate key wins). */
export function parseKeyValues(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const raw of text.split('\n')) {
    const line = raw.replace(/\r$/, '').trim();
    const eq = line.indexOf('=');
    if (line.startsWith('#') || eq <= 0) continue;
    out[line.slice(0, eq).trim()] = line.slice(eq + 1).trim();
  }
  return out;
}

/** A file's text, or null when the device has no such file. */
async function readText(c: Client, path: string): Promise<string | null> {
  try {
    return new TextDecoder().decode(await c.readFile(path));
  } catch (error) {
    if (isXyfError(error, 'not_found')) return null;
    throw error;
  }
}

/** The saved record by default. Integration tests can supply a configuration source. */
export let configSource: (c: Client) => Promise<SavedConfig | null> = async (c) => {
  const text = await readModsConfig(c);
  return text === null ? null : parseKeyValues(text);
};
export function setConfigSource(source: typeof configSource): void { configSource = source; }
async function rebootAndReconnect(): Promise<string> {
  const open = client;
  if (!open?.connected) throw new Error('connect before rebooting');
  const build = open.info.image;
  await open.reboot('menu');
  const info = await open.reconnect({ timeoutMs: 20_000 });
  if (info.image !== build) throw new Error('a different XY Forever build answered after reboot');
  const entries = await open.listDir('/yaffs2/xy-forever');
  return `HELLO ${info.proto} after restart, read ${entries.length} directory entries`;
}
/** For tests and the device browser test (scripts/device-browser-test.ts): the seam, reachable from outside the module. */
(window as Window & { xyfDemo?: unknown }).xyfDemo = { setConfigSource, rebootAndReconnect };

/** The saved configuration in words. */
export function configText(config: SavedConfig | null): string {
  if (!config) return 'No saved configuration: no mods.';
  const mods = (config['mods'] ?? '').split(',').filter(Boolean);
  const set = config['profile'] || config['generation'] ? ` (${config['profile'] ?? '?'}/${config['generation'] ?? '?'})` : '';
  const built = config['slot'] ? `built into ${config['slot']}` : 'not built yet';
  return `Base ${config['base'] || '-'}${set}${config['unverified'] === '1' ? ', unverified base allowed' : ''}; ` +
    `mods on: ${mods.length ? mods.join(', ') : 'none'}; ${built}.`;
}

/** content-state.txt in words (docs/storage/state.md, content state): no file or official, installed, switching. */
export function contentText(text: string | null, oldExists: boolean): string {
  if (text === null) return oldExists ? 'Content: needs repair (a switch did not finish).' : 'Content: official.';
  const s = parseKeyValues(text);
  if (s['state'] === 'installed') return `Content: installed for ${s['entry'] ?? '?'}.`;
  if (s['state'] === 'switching') return `Content: needs repair (the switch to ${s['target'] ?? '?'} did not finish).`;
  if (s['state'] === 'official' || !s['state']) return 'Content: official.';
  return `Content: unknown state ${s['state']}.`;
}

async function showConfig(): Promise<void> {
  el('config-saved').textContent = configText(await configSource(client!));
  const state = await readText(client!, '/yaffs2/xy-forever/content-state.txt');
  const old = state === null && await readText(client!, '/yaffs2/xy-forever/content-state.old') !== null;
  el('config-content').textContent = contentText(state, old);
}

el<HTMLInputElement>('mods-folder').addEventListener('change', () => {
  const input = el<HTMLInputElement>('mods-folder');
  const source = fileListSource([...input.files ?? []]);
  input.value = '';
  chosen = null;
  el('mods-chosen').textContent = '';
  action(async () => {
    const set = await localSet(source);
    chosen = { source, set };
    const files = [...set.packages.values()].flat();
    el('mods-chosen').textContent = `Chosen: ${set.profile}/${set.generation}, ${set.packages.size} packages, ${files.length} files, ` +
      human(files.reduce((n, f) => n + f.data.length, 0));
  })();
});

el('mods-send').addEventListener('click', action(async () => {
  const { source, set } = chosen!;
  if (!confirm(`Send ${set.packages.size} packages of ${set.profile}/${set.generation} to /yaffs2/xy-forever/mods/ on the OP-XY? ` +
    'Files already there with the same size are skipped.')) return;
  try {
    const s = await pushPackages(client!, source, {
      onPackage: (r) => modsProgress(`${r.name}: ${r.state === 'sent' ? `sent ${r.files} of ${r.of} files` : 'skipped'}`),
      onProgress: (done, total) => modsProgress(`Sending: ${human(done)} of ${human(total)}`),
    });
    modsProgress(`Sent ${s.sent} packages, skipped ${s.skipped}: ${s.files} files, ${human(s.bytes)} in ${s.seconds.toFixed(1)} s`);
  } finally {
    await showMods().catch(() => undefined);
  }
}));

// ---- Build on device ------------------------------------------------------------------------------

const buildProgress = (text: string): void => { el('build-progress').textContent = text; };
const buildResult = (text: string): void => { el('build-result').textContent = text; };

/** Fills a select with [value, label] options, keeping the current choice when it is still offered. */
function fill(select: HTMLSelectElement, options: readonly (readonly [string, string])[]): void {
  const was = select.value;
  select.replaceChildren(...options.map(([value, label]) => Object.assign(document.createElement('option'), { value, textContent: label })));
  if (options.some(([value]) => value === was)) select.value = was;
}

/** The generations held and the library entries holding an os.ldr (bases), then the chosen generation's checklist. */
async function showBuild(): Promise<void> {
  fill(el<HTMLSelectElement>('build-generation'), held.map((g) => [`${g.profile}/${g.generation}`, `${g.profile}/${g.generation}`] as const));
  fill(el<HTMLSelectElement>('build-base'), (await libraryBases(client!)).map((b) => [b.id, b.name === b.id ? b.id : `${b.id}: ${b.name}`] as const));
  showChoices();
}

/** The chosen generation's machines, then its extensions (complete packages, in info.txt order): name and kind, a checkbox each. */
function showChoices(): void {
  const gen = held.find((g) => `${g.profile}/${g.generation}` === el<HTMLSelectElement>('build-generation').value);
  const body = el('build-choices').querySelector('tbody')!;
  body.textContent = '';
  const choices = (gen?.packages ?? []).filter((p) => p.complete && p.info['type'] === 'mod').map((p) => {
    const extension = p.info['machine'] === 'extension';
    return { id: p.info['id'] ?? (extension ? p.name.replace(/^ext-/, '') : p.name), name: p.info['name'] ?? p.name, kind: p.info['machine'] ?? '?',
      extension, order: Number(p.info['order'] ?? 0) };
  }).sort((a, b) => Number(a.extension) - Number(b.extension) || a.order - b.order);
  if (choices.length === 0) {
    Object.assign(body.insertRow().insertCell(), { colSpan: 2, textContent: gen ? 'No complete machine or extension packages.' : 'No generation on the device.' });
    return;
  }
  for (const c of choices) {
    const row = body.insertRow();
    Object.assign(row.dataset, { id: c.id, kind: c.extension ? 'extension' : 'machine' });
    const box = Object.assign(document.createElement('input'), { type: 'checkbox', disabled: client === null || building !== null });
    const label = document.createElement('label');
    label.append(box, ` ${c.name}`);
    row.insertCell().append(label);
    Object.assign(row.insertCell(), { className: 'kind', textContent: c.extension ? 'extension' : c.kind });
  }
}

el('build-generation').addEventListener('change', showChoices);

const progressText = (s: JobStatus): string =>
  `Building on the device: job ${s.job}, ${s.phase}${s.total ? ` ${s.done}/${s.total}` : ''}, ${(s.elapsedMs / 1000).toFixed(1)} s`;

el('build-start').addEventListener('click', action(async () => {
  const [profile = '', generation = ''] = el<HTMLSelectElement>('build-generation').value.split('/');
  const picked = [...el('build-choices').querySelectorAll<HTMLTableRowElement>('tbody tr')].filter((r) => r.querySelector('input')?.checked);
  const machines = picked.filter((r) => r.dataset['kind'] === 'machine').map((r) => r.dataset['id']!);
  const extensions = picked.filter((r) => r.dataset['kind'] === 'extension').map((r) => r.dataset['id']!);
  const base = el<HTMLSelectElement>('build-base').value, name = el<HTMLInputElement>('build-name').value.trim();
  buildProgress('');
  if (!generation || !base || machines.length === 0 || !name) {
    buildResult('Choose a generation, a base entry, at least one machine and a name.');
    return;
  }
  if (!confirm(`Build "${name}" on the OP-XY from ${base} with ${[...machines, ...extensions].join(', ')} (${profile}/${generation})? ` +
    'The result replaces the older of the Firmware library entries mods-a and mods-b (never the default).')) return;
  buildResult('');
  built = null;
  building = new AbortController();
  show('working');
  try {
    const { entry, status: s } = await applyMods(client!, { base, profile, generation, machines, extensions, name, unverified: el<HTMLInputElement>('build-unverified').checked },
      { signal: building.signal, onProgress: (status) => buildProgress(progressText(status)) });
    built = entry;
    buildResult(`built: ${entry} is in the Firmware library (content not installed); ${(s.elapsedMs / 1000).toFixed(1)} s, ${s.steps} steps`);
  } catch (error) {
    if (error instanceof JobError && error.status.phase === 'cancelled') {
      buildResult(`Cancelled: ${error.status.debris ? `files may remain in ${error.status.folder}/` : 'the OP-XY removed what it wrote'}`);
    } else if (error instanceof JobError || (isXyfError(error) && error.command === 'mods.apply')) {
      buildResult(`Build failed: ${error.message.replace(/^mods\.apply: |^the build failed: /, '')}`);
    } else {
      throw error;
    }
  } finally {
    building = null;
  }
}));

el('build-default').addEventListener('click', action(async () => {
  const entry = built;
  if (entry === null) return;
  if (!confirm(`Make ${entry} the default firmware? The OP-XY first installs its content: it rewrites files in /yaffs2/content/ ` +
    '(the official content is saved and comes back when a stock entry is made the default). This cannot be cancelled once it starts.')) return;
  buildResult('');
  try {
    const r = await makeDefault(client!, entry, { onProgress: (s) => buildProgress(
      `Installing the content of ${entry}: ${s.phase}${s.total ? ` ${s.done}/${s.total}` : ''}, ${(s.elapsedMs / 1000).toFixed(1)} s`) });
    buildResult(`${entry} is the default firmware${r.switched ? ' (its content is installed)' : ''}`);
  } catch (error) {
    if (!(error instanceof JobError)) throw error;
    buildResult(`Make default failed: ${error.message.replace(/^the content switch failed: /, '')}; the default is unchanged`);
  }
}));

el('build-cancel').addEventListener('click', () => {
  building?.abort();
  el<HTMLButtonElement>('build-cancel').disabled = true;
  buildProgress('Cancelling: the OP-XY removes what it wrote ...');
});
