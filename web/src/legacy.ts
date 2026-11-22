// The pages not moved to React yet: Install, Mods, Files. Plain DOM in the shell's sections; one device
// action at a time.
import { RELEASE_FILTERS, configGet, configSet, fileListSource, isXyfError, listPackages, localSet, pushPackages, removePackage } from 'xy-forever-client';
import type { Client, DirEntry, HeldGeneration, LocalSet, PackageSource, SavedInputs } from 'xy-forever-client';
import { ROOT, WRITE_RULE, changeRefusal, folderWritable, groupMods, human, joinPath, nameProblem, parentOf } from './model.ts';
import type { SavedConfig } from './model.ts';
import { h } from './ui.ts';
import { confirmAction, promptText } from './ask.tsx';
import { notice } from './notice.ts';
import { useConnection } from './connection.ts';
import { adopt, canConnect, deviceBusy, openDevice, runAction as run, savedConfig as savedConfigOf } from './device.ts';
import { downloadBlob } from './lib/download.ts';
import { installPage } from './install/page.ts';
import type { Lease } from './install/page.ts';
import { navigatePath as navigate } from './routing.ts';
export type LegacyPage = 'mods' | 'files';

const dev = (): Client => useConnection.getState().client!;
const page = (id: string): HTMLElement => document.getElementById(`page-${id}`)!;

// ---- Pages and actions -------------------------------------------------------------------------------

const loaders: Record<LegacyPage, () => Promise<void>> = {
  mods: () => run('list_packages', loadMods),
  files: () => run('browse_files', () => browse(cwd)),
};

/** Renders a page into its section (the router mounts it). */
export function showPage(p: LegacyPage): void { loadedOther = true; void loaders[p](); }

let shownInstall = false;
/** The install flow's one section, kept across its stages. */
export function installSection(): HTMLElement {
  install ??= installPage(installRoot, installDeps());
  return installRoot;
}
// Uninstalled: the install flow starts over.
useConnection.subscribe((now, before) => { if (now.stockRestored && !before.stockRestored) install?.reset(); });
const installRoot = Object.assign(document.createElement('section'), { id: 'page-install', className: 'page', hidden: true });
/** Shows an install stage: the first page this document shows counts as the load (a reload asks te-boot again). */
export function showInstall(stage: string | undefined): void {
  const how = shownInstall || loadedOther ? 'push' : 'load';
  shownInstall = true;
  installSection();
  install!.show(`/install/${stage ?? ''}`, how);
}
let loadedOther = false;

const btn = (label: string, onclick: () => void, cls = ''): HTMLButtonElement => h('button', { type: 'button', class: cls, onclick }, label);

// ---- Install (#178) -----------------------------------------------------------------------------------

let install: ReturnType<typeof installPage> | null = null;

function installDeps(): Parameters<typeof installPage>[1] {
  const nav = navigator as Navigator & { requestMIDIAccess?: (o: { sysex: boolean }) => Promise<unknown>; usb?: unknown;
    serial?: { addEventListener?: (type: string, listener: () => void) => void } };
  return {
    midi: nav.requestMIDIAccess ? nav.requestMIDIAccess.bind(nav) : null,
    usb: nav.usb ?? null,
    serial: canConnect(),
    connect: async () => own() ?? lease(await openDevice({ filters: RELEASE_FILTERS })),
    find: findDevice,
    handoff: (lease) => {                     // installed: the connection becomes the app's, then the Mods page
      if (useConnection.getState().client !== lease.client) adopt(lease.client);
      useConnection.setState({ installed: true });
      navigate('/mods');
    },
    onPort: (listener) => nav.serial?.addEventListener?.('connect', listener),
  };
}

/** This app's connection, lent (close keeps it): while it holds the port, the Install page uses it and never opens a
 * second link (two readers on one OP-XY split its replies). */
function own(): Lease | null { const { client } = useConnection.getState(); return client?.connected ? { client, close: async () => {} } : null; }
const lease = (found: Client): Lease => ({ client: found, close: () => found.close() });

/** XY Forever without the chooser, for the Install page to detect: this app's connection, else a granted port that
 * answers (or the simulator); null when none does. */
async function findDevice(): Promise<Lease | null> {
  const mine = own();
  if (mine) return mine;
  if (deviceBusy() || !canConnect()) return null;
  return lease(await openDevice({ timeoutMs: 5_000, filters: RELEASE_FILTERS, choose: false }));
}

// ---- Mods --------------------------------------------------------------------------------------------

let chosen: { source: PackageSource; set: LocalSet } | null = null;
let held: HeldGeneration[] = [];

async function savedConfig() { return savedConfigOf(dev()); }

async function loadMods(): Promise<void> {
  const c = dev();
  held = await listPackages(c);
  const { config, error: configError } = await savedConfig();
  const inputs = configError ? null : await configGet(c).catch((error: unknown) => { if (isXyfError(error, 'update_required', 'unsupported')) return null; throw error; });
  const root = page('mods');
  const picker = h('input', { type: 'file', webkitdirectory: true, multiple: true, onchange: () => void pick(picker) });
  root.replaceChildren(
    h('h1', {}, 'Mods'),
    h('p', { class: 'lede' }, 'Mod packages stored on the OP-XY, and the mods it runs.'),
    ...(configError === undefined ? [] : [h('p', { class: 'muted' }, `Could not read the saved configuration (${configError}).`)]),
    h('div', { class: 'card' },
      h('div', { class: 'row' }, h('h2', { style: 'margin:0' }, 'Add packages'), h('span', { class: 'spacer' }),
        h('label', { class: 'button' }, 'Choose package folder...', picker),
        btn('Send to OP-XY', () => void run('send_packages', send, 'Sending packages...'), 'primary')),
      h('p', { class: 'muted', id: 'mods-chosen' }, chosen ? chosenText(chosen.set) : 'Choose a generation folder (from modkit split): it holds base/ and one folder per mod.'),
      h('progress', { id: 'mods-progress', value: 0, max: 1, hidden: true })),
    ...(held.length === 0 ? [h('div', { class: 'card muted' }, 'No mod packages on this OP-XY yet.')] : held.map((g) => generationCard(g, config))),
    inputsCard(inputs),
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
        btn('Remove all', () => void run('remove_packages', () => remove(g.generation, null, `the ${kind} ${g.generation} folder`)), 'small danger')),
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
        h('td', { class: 'actions' }, btn('Remove', () => void run('remove_packages', () => remove(r.folder, g.generation, r.name)), 'small danger'))));
    }
  }
  const baseHeld = g.packages.some((p) => p.name === 'base');
  return h('div', { class: 'card' },
    h('div', { class: 'row' }, h('h2', { style: 'margin:0' }, `${g.profile} ${g.generation}`), !baseHeld && h('span', { class: 'tag warn' }, 'no base package'),
      h('span', { class: 'spacer' }), btn('Remove all', () => void run('remove_packages', () => remove(g.generation, null, `the whole ${g.generation} package set`)), 'small danger')),
    h('p', { class: 'muted' }, config === undefined ? 'On/off: the saved configuration could not be read.' : sameSet ? 'On/off: as saved on the device.' : 'On/off: this set is not the saved one, so all are off.'),
    h('table', {}, body));
}

async function pick(input: HTMLInputElement): Promise<void> {
  const source = fileListSource([...input.files ?? []]);
  input.value = '';
  chosen = null;
  await run('choose_packages', async () => {
    chosen = { source, set: await localSet(source) };
    document.getElementById('mods-chosen')!.textContent = chosenText(chosen.set);
  });
}

async function send(): Promise<void> {
  if (!chosen) throw new Error('Choose a package folder first.');
  const bar = document.getElementById('mods-progress') as HTMLProgressElement;
  bar.hidden = false;
  const summary = await pushPackages(dev(), chosen.source, { onProgress: (done, total) => { bar.max = total || 1; bar.value = done; } });
  chosen = null;
  await loadMods();
  notice(`Sent ${summary.sent} package${summary.sent === 1 ? '' : 's'} (${human(summary.bytes)}); ${summary.skipped} already there.`);
}

async function remove(what: string, generation: string | null, label: string): Promise<void> {
  const opts = generation ? { generation } : {};
  const plan = await removePackage(dev(), what, { ...opts, dryRun: true });
  const files = plan.reduce((n, r) => n + r.files, 0);
  if (!await confirmAction(`Remove ${label}?`, `${files} files (${human(plan.reduce((n, r) => n + r.bytes, 0))}) are deleted from the OP-XY. A saved build that uses it keeps working until you change mods.`, 'Remove', true)) return;
  await removePackage(dev(), what, opts);
  await loadMods();
}

/**
 * The mods the OP-XY runs (#26): saved as turning them on in the device's Mods menu does (config.set). The device checks
 * that it can build them and keeps them, or says why not; it builds them the next time XY Forever starts.
 */
function inputsCard(saved: SavedInputs | null): HTMLElement {
  const card = h('div', { class: 'card', id: 'inputs' });
  const gens = held.filter((g) => g.packages.some((p) => p.name === 'base' && p.complete));
  card.append(h('h2', {}, 'Mods on the OP-XY'),
    h('p', { class: 'muted' }, 'Saving works like turning mods on in the device\'s Mods menu: the OP-XY checks that it can build them and keeps them. It builds them the next time it starts.'));
  if (!saved) { card.append(h('p', {}, 'This OP-XY\'s XY Forever cannot set mods over USB (it needs protocol 1.24): update it first.')); return card; }
  if (gens.length === 0) { card.append(h('p', {}, 'Send a package set with its base first.')); return card; }
  const g = gens.find((x) => x.generation === saved.generation) ?? gens[0]!;
  const checks = h('div', { class: 'checks' }, ...groupMods(g.packages.filter((p) => p.complete), null).flatMap((grp) => grp.rows).map((r) =>
    h('label', {}, h('input', { type: 'checkbox', value: r.folder, checked: saved.mods.includes(r.folder) }), `${r.name}`, h('span', { class: 'muted' }, r.kind))));
  const memory = h('input', { type: 'checkbox', checked: saved.expanded });
  const unknown = h('input', { type: 'checkbox', checked: saved.unverified });
  const status = h('p', { class: 'muted', id: 'inputs-status' }, saved.write === 'failed' ? 'The last change could not be written: save again.' : '');
  const save = btn('Save', () => void run('save_mods', async () => {
    const mods = [...checks.querySelectorAll<HTMLInputElement>('input:checked')].map((i) => i.value);
    try {
      await configSet(dev(), { mods, expanded: memory.checked, unverified: unknown.checked });
      status.textContent = 'Saved. The OP-XY builds and installs it the next time it starts.';
    } catch (error) {
      if (!isXyfError(error, 'refused')) throw error;
      status.textContent = `Not saved: ${error.message}`;
    }
  }, 'Saving on the OP-XY...'), 'primary');
  card.append(checks,
    h('div', { class: 'row' }, h('label', {}, memory, '4 MiB mod memory'), h('label', {}, unknown, 'Allow an unknown base')),
    h('div', { class: 'row' }, save), status);
  return card;
}

// ---- Files -------------------------------------------------------------------------------------------

let cwd = ROOT;

async function browse(path: string): Promise<void> {
  const entries = await dev().listDir(path);
  cwd = path.replace(/\/+$/, '') || ROOT;
  entries.sort((a, b) => Number(a.kind !== 'folder') - Number(b.kind !== 'folder') || a.name.localeCompare(b.name));
  const writable = folderWritable(cwd);
  const parts = cwd.split('/').filter(Boolean);
  const crumbs = h('nav', { class: 'crumbs', 'aria-label': 'Folder' }, ...parts.flatMap((p, i) => {
    const here = '/' + parts.slice(0, i + 1).join('/');
    return [i > 0 ? '/' : '', i === parts.length - 1 ? h('b', {}, p) : btn(p, () => void run('browse_files', () => browse(here)))];
  }));
  const upload = h('input', { type: 'file', multiple: true, onchange: () => void run('upload_files', () => uploadFiles(upload), 'Uploading...') });
  const body = h('tbody', { id: 'files-body' });
  if (cwd !== ROOT) body.append(h('tr', {}, h('td', { colspan: 3 }, h('button', { type: 'button', class: 'linkish', onclick: () => void run('browse_files', () => browse(parentOf(cwd))) }, '.. (up)'))));
  for (const e of entries) body.append(fileRow(e, writable));
  if (entries.length === 0) body.append(h('tr', {}, h('td', { colspan: 3, class: 'muted' }, 'Empty folder.')));
  const progress = h('progress', { id: 'files-progress', value: 0, max: 1, hidden: true });
  page('files').replaceChildren(
    h('h1', {}, 'Files'),
    h('p', { class: 'lede' }, 'Browse the OP-XY\'s storage. Everything can be downloaded.'),
    h('p', { class: 'rules', 'data-testid': 'files-rules' }, `Changes are allowed ${WRITE_RULE}. `, writable ? 'This folder accepts changes.' : 'This folder is read-only.'),
    h('div', { class: 'card' },
      h('div', { class: 'row' }, crumbs, h('span', { class: 'spacer' }),
        writable && h('label', { class: 'button' }, 'Upload...', upload),
        writable && btn('New folder', () => void run('new_folder', newFolder))),
      progress,
      h('table', {}, h('thead', {}, h('tr', {}, h('th', {}, 'Name'), h('th', { class: 'hide-narrow' }, 'Size'), h('th', {}))), body)),
  );
}

function fileRow(e: DirEntry, writable: boolean): HTMLElement {
  const path = joinPath(cwd, e.name);
  const name = e.kind === 'folder'
    ? h('button', { type: 'button', class: 'linkish', onclick: () => void run('browse_files', () => browse(path)) }, `${e.name}/`)
    : h('span', {}, e.name);
  return h('tr', { 'data-name': e.name },
    h('td', {}, name),
    h('td', { class: 'muted hide-narrow' }, e.kind === 'file' ? human(e.size) : ''),
    h('td', { class: 'actions' },
      e.kind === 'file' && btn('Download', () => void run('download_file', () => download(path, e.name), 'Downloading...'), 'small'),
      e.kind !== 'other' && btn('Copy', () => void run('copy_file', () => copy(path, e)), 'small'),
      writable && e.kind !== 'other' && btn('Rename/Move', () => void run('move_file', () => move(path)), 'small'),
      writable && e.kind !== 'other' && btn('Delete', () => void run('delete_file', () => removePath(path, e)), 'small danger')));
}

function bar(): HTMLProgressElement { const b = document.getElementById('files-progress') as HTMLProgressElement; b.hidden = false; return b; }

async function download(path: string, name: string): Promise<void> {
  const b = bar();
  const data = await dev().readFile(path, { onProgress: (done) => { b.removeAttribute('value'); b.title = human(done); } });
  downloadBlob(new Blob([data as BlobPart]), name);
  b.hidden = true;
}

async function uploadFiles(input: HTMLInputElement): Promise<void> {
  const files = [...input.files ?? []];
  input.value = '';
  const existing = new Set((await dev().listDir(cwd)).map((e) => e.name));
  const replaced = files.filter((f) => existing.has(f.name)).map((f) => f.name);
  if (replaced.length && !await confirmAction('Replace files?', `These are replaced on the OP-XY:\n${replaced.join('\n')}`, 'Replace', true)) return;
  const b = bar();
  for (const f of files) {
    const target = joinPath(cwd, f.name);
    const why = changeRefusal(target);
    if (why) throw new Error(why);
    await dev().writeFile(target, new Uint8Array(await f.arrayBuffer()), { onProgress: (done, total) => { b.max = total ?? 1; b.value = done; } });
  }
  await browse(cwd);
}

async function newFolder(): Promise<void> {
  const name = await promptText('New folder', `In ${cwd}`, '', 'Create');
  if (name === null) return;
  const problem = nameProblem(name) ?? changeRefusal(joinPath(cwd, name), true);
  if (problem) throw new Error(problem);
  await dev().makeDir(joinPath(cwd, name));
  await browse(cwd);
}

async function move(path: string): Promise<void> {
  const target = await promptText('Rename or move', `Type the new full path for ${path}. Nothing is replaced.`, path, 'Rename');
  if (target === null || target === path) return;
  const why = changeRefusal(target);
  if (why) throw new Error(why);
  await dev().renamePath(path, target);
  await browse(cwd);
}

async function copy(path: string, e: DirEntry): Promise<void> {
  const suggestion = folderWritable(cwd) ? `${path}-copy` : joinPath('/yaffs2/content', e.name);
  const target = await promptText(`Copy ${e.kind}`, `Type the full path of the copy. It must be ${WRITE_RULE}.`, suggestion, 'Copy');
  if (target === null) return;
  const why = changeRefusal(target);
  if (why) throw new Error(why);
  await dev().copyTree(path, target);
  await browse(cwd);
}

async function removePath(path: string, e: DirEntry): Promise<void> {
  if (e.kind === 'folder') {
    const inside = await dev().listDir(path);
    if (inside.length) throw new Error(`${e.name} is not empty: delete what it holds first.`);
  }
  if (!await confirmAction(`Delete ${e.name}?`, `${path} is deleted from the OP-XY. This cannot be undone.`, 'Delete', true)) return;
  if (e.kind === 'folder') await dev().removeDir(path); else await dev().deleteFile(path);
  await browse(cwd);
}
