#!/usr/bin/env node
// Runs open-xy's own builder/core.mjs (unmodified, from an open-xy tree) on a list of jobs for
// xy_forever/modkit.py: openKit, then linkKit + patchFirmware (website mode: profiling off) +
// buildAssets, or only assignNumbers + buildAssets (assets_only). Writes one result per job:
// SHA-256 digests of everything the build produces, so two kits can be compared byte for byte.
// With link_out (xy_forever/modpatch.py, plan 5.4c) a job also writes linkKit's result there
// (module.bin, link.json: globals, services, extensions, numbers, sizes) and the patched os.ldr.
// A job with expanded: true links, patches and builds assets with the 4 MiB option (core.memoryKit).
// Host only: no device, no network.
//   node scripts/modkit_core.mjs --core OPEN_XY/builder/core.mjs --jobs jobs.json --out results.json
import {readFileSync, writeFileSync, mkdirSync} from 'node:fs';
import {dirname, join} from 'node:path';
import {pathToFileURL} from 'node:url';
import {createHash} from 'node:crypto';
import {parseArgs} from 'node:util';

const {values} = parseArgs({options: {core: {type: 'string'}, jobs: {type: 'string'}, out: {type: 'string'}}});
const core = await import(pathToFileURL(values.core).href);
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const text = value => digest(Buffer.from(JSON.stringify(value)));
const sorted = map => [...map].sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
const opened = new Map();

async function open(path) {
  if (!opened.has(path)) opened.set(path, await core.openKit(JSON.parse(readFileSync(path, 'utf8'))));
  return opened.get(path);
}

async function run(job) {
  // expanded (XY Forever 4.8m1): the website's "Use 4 MiB for machines" option, core.memoryKit(kit, true).
  const kit = job.expanded ? core.memoryKit(await open(job.kit), true) : await open(job.kit);
  const catalog = core.decode64(kit.stockCatalog);
  const result = {name: job.name};
  let numbers;
  if (job.assets_only) {
    numbers = core.assignNumbers(kit, job.machines, null);
  } else {
    const linked = core.linkKit(kit, job.machines, null, job.extensions);
    numbers = linked.numbers;
    Object.assign(result, {
      module_sha256: digest(linked.module), module_bytes: linked.module.length,
      globals_sha256: text(sorted(linked.globals)), aliases_sha256: text(sorted(linked.aliases)),
      objects: linked.objects.map(o => o.name), services: [...linked.services].sort(),
      extensions: linked.extensions, numbers, sizes: linked.sizes,
      segments_sha256: text(linked.segments),
    });
    if (job.link_out) {
      mkdirSync(job.link_out, {recursive: true});
      writeFileSync(join(job.link_out, 'module.bin'), linked.module);
      writeFileSync(join(job.link_out, 'link.json'), JSON.stringify({
        globals: sorted(linked.globals), services: [...linked.services].sort(), extensions: linked.extensions,
        numbers: linked.numbers, sizes: linked.sizes}));
    }
    if (job.stock) {
      const os = await core.patchFirmware(kit, linked, new Uint8Array(readFileSync(job.stock)), {profiling: false});
      result.ldr_sha256 = digest(os);
      result.ldr_bytes = os.length;
      if (job.link_out) writeFileSync(join(job.link_out, 'os.ldr'), os);
    }
  }
  const files = await core.buildAssets(kit, job.machines, catalog, numbers, {}, job.extensions);
  result.files = Object.fromEntries(Object.entries(files).map(([path, bytes]) => [path, digest(bytes)]));
  result.file_order = Object.keys(files);
  if (job.write) for (const [path, bytes] of Object.entries(files)) {
    const target = join(job.write, path);
    mkdirSync(dirname(target), {recursive: true});
    writeFileSync(target, bytes);
  }
  return result;
}

const results = [];
for (const job of JSON.parse(readFileSync(values.jobs, 'utf8'))) {
  try {
    results.push(await run(job));
  } catch (error) {
    results.push({name: job.name, error: String(error.message)});
  }
}
writeFileSync(values.out, JSON.stringify(results));
