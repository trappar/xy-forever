#!/usr/bin/env node
// Reference for the native mod linker (xy_forever/modlink.py): runs open-xy's own
// builder/core.mjs (unmodified) linkKit on each job's kit and prints everything the native linker
// must reproduce: module digest, globals, aliases, numbers, objects, services, extensions, sizes,
// or core.mjs's error text. open: "full" = openKit (the reviewed profile only); "light" = what
// openKit checks of objects and numbers (readObject, accessors, validRequest, fixed numbers), for
// synthetic kits whose profile is not the reviewed one. Jobs {objects: [path]} run readObject alone.
// Host only.   node scripts/modlink_core.mjs --core OPEN_XY/builder/core.mjs --jobs J --out R
import {readFileSync, writeFileSync} from 'node:fs';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
import {parseArgs} from 'node:util';

const {values} = parseArgs({options: {core: {type: 'string'}, jobs: {type: 'string'}, out: {type: 'string'}}});
const core = await import(pathToFileURL(values.core).href);
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const kits = new Map();

function light(kit) {
  const rules = kit.profile.identities;
  if (!(kit.machines.length > 0 && kit.machines.length <= rules.engines)) throw new Error('Unsupported curated kit');
  for (const m of kit.machines) {
    if (!core.validRequest(rules, m.kind, m.identity)) throw new Error('Machine numbers overlap a stock slot or exceed capacity: ' + m.id);
    const f = kit.fixed[m.id];
    if (f && !(Object.keys(m.identity).length === Object.keys(f).length &&
               Object.keys(m.identity).every(k => m.identity[k] === null || m.identity[k] === f[k])))
      throw new Error('Fixed machine numbers differ: ' + m.id);
  }
  core.numberLayout(rules, Object.entries(kit.fixed).map(([id, identity]) => [id, null, identity]), null, []);
  if ((kit.extensions ?? []).length > 16) throw new Error('Invalid extension collection');
  const objects = kit.objects.map(o => ({...o, ...core.readObject(core.decode64(o.bytes))}));
  for (const e of kit.extensions ?? []) {
    const owned = objects.filter(o => o.extension === e.id);
    const has = suffix => owned.length === 1 && owned[0].symbols.some(s => s.section && s.binding === 1 &&
      s.name === 'sdk_extension_' + e.id + '_' + suffix);
    if (!has('hooks')) throw new Error('Missing extension accessor: ' + e.id);
    if ((e.master_processor ?? e.master_filter) && !((has('processor') && has('panel')) || has('filter')))
      throw new Error('Missing master processor accessors: ' + e.id);
  }
  return {...kit, objects};
}

async function open(job) {
  const key = job.kit + '\n' + job.open;
  if (!kits.has(key)) {
    const kit = JSON.parse(readFileSync(job.kit, 'utf8'));
    try { kits.set(key, {kit: job.open === 'light' ? light(kit) : await core.openKit(kit)}); }
    catch (error) { kits.set(key, {error: String(error.message)}); }
  }
  const opened = kits.get(key);
  if (opened.error) throw new Error(opened.error);
  return opened.kit;
}

async function run(job) {
  if (job.objects) return {name: job.name, objects: job.objects.map(path => {
    try { core.readObject(new Uint8Array(readFileSync(path))); return null; } catch (error) { return String(error.message); }
  })};
  const kit = await open(job);
  const linked = core.linkKit(kit, job.machines, job.layout ?? null, job.extensions);
  return {name: job.name, module_sha256: digest(linked.module), module_bytes: linked.module.length,
    globals: Object.fromEntries(linked.globals), aliases: Object.fromEntries(linked.aliases),
    numbers: linked.numbers, objects: linked.objects.map(o => o.name), services: [...linked.services].sort(),
    extensions: linked.extensions, sizes: linked.sizes};
}

const results = [];
for (const job of JSON.parse(readFileSync(values.jobs, 'utf8'))) {
  try { results.push(await run(job)); } catch (error) { results.push({name: job.name, error: String(error.message)}); }
}
writeFileSync(values.out, JSON.stringify(results));
