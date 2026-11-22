#!/usr/bin/env python3
"""Mod boot: a host-assembled mod firmware into XY Forever's library, booted once.

Superseded since the content switch: its entry has no content of its own, so the menu lists it
"content not installed" and its content stage writes /yaffs2/content/ directly. A host-built test image goes
in with scripts/test_entry.py (an assembled entry the device's content switch installs).

Same conventions as scripts/device_pass.py (whose Pass helpers it reuses): every device command
goes through xyf_host.py's main(); `--sim HOST:PORT,CONTROL`; `--plan`; a PASS/FAIL/SKIP table; the first
FAIL stops; no answer: one `NEED:` line, exit 3, no folder. It writes only firmware/<id>/ (the
scratch entry) and, with --content, the content delta under content/; never user/, other entries,
the state slots or the default.

Stages:
  assemble   host: native assembly (modpatch: link, patch, content) of --mods (default fm2op);
             patch ok (cold LDR policy, ldr::inspect), start_verify ok, base known, entry
             0x11a00000; library-add's LDR checks; size and SHA-256. The content delta stays in
             the evidence folder (without it the machines play but are not listed).
             --image FILE uses an image assembled earlier (the LDR checks only).
  upload     ls firmware/: <id> absent (--replace: overwritten), not the default; mkdir; put
             os.ldr; put info.txt (strict manifest: type=firmware name version kind bytes crc32
             sha256); ls sizes; get os.ldr whole: SHA-256 equal; after the write window closed the
             menu catalog lists <id> usable, firmware/ lists it, the slots' default is unchanged.
  content    --content [DIR] only: get content/op1_factory.json into the evidence folder
             (SHA-256 recorded; if that fails nothing is written), and every delta file that exists
             already; then mkdir the missing folders, put display/<id>.svg and presets/<id>/*.json,
             and the assembled op1_factory.json last (each read back). DIR: a content delta made
             earlier (needed with --image); default the assembled one.
  accept     SKIP: no dry run of a named entry exists (CONTINUE mode 1 dry-runs the default only).
  boot-once  --boot-once only: slots (default D, no once); the menu catalog lists <id> usable;
             `boot <id> --yes-boot` (library.boot: the menu's "boot once", no state write); USB
             goes. The OS does not answer this protocol: the script prints what to look for, the
             recovery line, and exits.
  cleanup    --cleanup (alone): slots: default = the one recorded by the last boot run (or
             --expect-default), no once; content (when the newest run wrote it, or with
             --official-catalog PATH): put the saved op1_factory.json (or PATH) back and get it:
             SHA-256 equal; files the delta replaced put back, files and folders it added removed;
             rm os.ldr, info.txt, rmdir (unless --keep); ls: gone.
Evidence: ../private/xy-forever/sessions/<date>-mod-boot-<n>/ (sim: -sim-mod-boot-, removed when
everything passed unless --keep-evidence). Exit 0 nothing failed, 1 FAIL, 2 usage, 3 NEED.
"""

import argparse
import hashlib
import json
import sys
import time
import zlib
from datetime import UTC, datetime
from pathlib import Path

import device_pass as dp

from xy_forever import modpatch, package_records, paths, xyf_entry
from xy_forever import xyf_host as host
from xy_forever.ldr import inspect_ldr, parse_ldr

StepFail, StepSkip = dp.StepFail, dp.StepSkip
SCRATCH = 'xyf-mods-test'
LIBRARY = dp.LIBRARY
ENTRY = 0x11A00000
PACKAGES = paths.PUBLISHED_MODS
CATALOG = paths.CATALOG_121
PUT_KIB_S, GET_KIB_S = 158.0, 168.0  # measured in a build 166 device pass
LOOK_FOR = (
    'On the OP-XY, look for: the screen goes dark, then the normal OP-XY 1.2.1 start-up and its main screen '
    'within about 15 s; keys and sound work (play a few notes on track 1). The mod machines are in the '
    'menus only if --content wrote the content delta. A black screen, a freeze or a restart '
    'loop: the image did not boot.'
)
CONTENT = '/yaffs2/content'
CATALOG_LEAF = 'op1_factory.json'
RECOVERY = (
    "RECOVERY: power off, then power on holding PLAY, release at the logo: XY Forever's menu returns "
    'and the default (%s) is unchanged. Then: uv run scripts/mod_boot.py --cleanup'
)

STAGES = (
    ('check', (('check', 'HELLO + STATUS (else NEED, exit 3)'),)),
    (
        'assemble',
        (('assemble', 'native assembly; patch/verify ok, base known, entry 0x11a00000; LDR checks; SHA-256'),),
    ),
    (
        'upload',
        (
            ('entry-free', 'ls firmware/: <id> absent (or --replace), not the default'),
            ('put-os', 'mkdir firmware/<id>; put os.ldr (~%g KiB/s)' % PUT_KIB_S),
            ('put-info', 'put info.txt (strict manifest, type=firmware)'),
            ('ls-entry', 'ls firmware/<id>: both files, right sizes'),
            ('read-back', 'get os.ldr (whole file, ~%g KiB/s): SHA-256 equal' % GET_KIB_S),
            ('library', 'window closed: menu catalog lists <id> usable; firmware/ lists it; default unchanged'),
        ),
    ),
    (
        'content',
        (
            (
                'content-save',
                'get content/op1_factory.json (SHA-256) and any delta file already there; '
                'no saved catalog: FAIL, nothing written',
            ),
            (
                'content-put',
                'mkdir missing folders; put display/<id>.svg, presets/<id>/*.json, then '
                'op1_factory.json (each read back)',
            ),
        ),
    ),
    ('accept', (('dry-run-named', 'dry run of a named entry (none exists: SKIP)'),)),
    (
        'boot-once',
        (
            ('boot-prepare', 'slots: the default, no once; menu catalog lists <id> usable'),
            ('boot', 'boot <id> --yes-boot (library.boot, once); USB goes'),
        ),
    ),
    (
        'cleanup',
        (
            ('cleanup-state', 'slots: default = the recorded one, no once'),
            (
                'cleanup-content',
                'catalog put back (saved or --official-catalog), SHA-256 read back; '
                'replaced files put back, added files and folders removed',
            ),
            ('cleanup-remove', 'rm os.ldr, info.txt, rmdir (unless --keep); ls firmware/: gone'),
        ),
    ),
    ('end', (('keepalive', 'real device, not booted: scripts/keepalive.sh on the folder'),)),
)


def assemble(generation, machines, stock, catalog, out, official=None, extensions=(), unverified=False):
    """modpatch's native job into out (os.ldr, content/, files.sha256); the adapter's report."""
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    binary = modpatch.build()
    job = out.parent / 'job.txt'
    job.write_text(
        modpatch.job_text(
            generation, list(machines), list(extensions), stock, catalog, official=official, unverified=unverified
        )
    )
    return modpatch.run(binary, job, out)


def image_problems(data, report=None, known=True):
    """What keeps this image out of the library: library-add's LDR rules and, for an assembled image,
    the adapter's checks (patch includes ldr::inspect with target/cold.cpp's policy)."""
    problems = []
    try:
        records = parse_ldr(data)
        inspect_ldr(data)
        if records[-1].target != ENTRY:
            problems.append('entry %#x, not %#x' % (records[-1].target, ENTRY))
    except Exception as error:
        problems.append('not an LDR stream: %s' % error)
    if not data or len(data) > host.IMAGE_CAPACITY:
        problems.append('size %d outside 1..%d' % (len(data), host.IMAGE_CAPACITY))
    if report is not None:
        steps = tuple(report.get(k) for k in ('input', 'link_status', 'patch', 'verify', 'content', 'copy'))
        if steps != ('ok',) * 6:
            problems.append(
                'assembly input/link/patch/verify/content/copy = %s (%s)'
                % ('/'.join(map(str, steps)), report.get('patch_detail') or report.get('link_detail') or '')
            )
        if report.get('entry') != ENTRY:
            problems.append('adapter entry %s' % report.get('entry'))
        if report.get('sha256') != hashlib.sha256(data).hexdigest():
            problems.append('report SHA-256 differs from os.ldr')
        if known and report.get('base') != 'known':
            problems.append('base %s (not in native/mod_known_bases.inc)' % report.get('base'))
    return problems


def info_for(data, name, version):
    return xyf_entry.manifest_text(
        dict(
            type='firmware',
            name=name,
            version=version,
            kind='open-xy',
            bytes=len(data),
            crc32='%08x' % (zlib.crc32(data) & 0xFFFFFFFF),
            sha256=hashlib.sha256(data).hexdigest(),
        )
    ).encode('ascii')


class ModBoot(dp.Pass):
    allow_boot = True  # its boot step: library.boot of the scratch entry

    def entry_path(self, leaf=''):
        return '%s/%s%s' % (LIBRARY, self.opts.id, '/' + leaf if leaf else '')

    # -- assemble ------------------------------------------------------------------------------
    def step_assemble(self):
        o = self.opts
        if o.image:
            data = Path(o.image).read_bytes()
            problems, report = image_problems(data), None
        else:
            missing = [str(p) for p in (o.stock, o.catalog) if not Path(p).is_file()]
            if not package_records.local_name(o.packages / 'base'):
                missing.append(str(o.packages / 'base'))
            if missing:
                raise StepFail('inputs missing: %s' % ', '.join(missing))
            out = self.folder / 'assembled'
            start = time.monotonic()
            report = assemble(o.packages, o.machines, o.stock, o.catalog, out)
            (self.folder / 'assembled-report.json').write_text(json.dumps(report, indent=1) + '\n')
            data = (out / 'os.ldr').read_bytes() if (out / 'os.ldr').is_file() else b''
            problems = image_problems(data, report)
            self.note('assembled in %.1f s' % (time.monotonic() - start))
        if problems:
            raise StepFail('; '.join(problems))
        self.state['image'] = data
        self.state['sha256'] = hashlib.sha256(data).hexdigest()
        delta = (
            ''
            if report is None
            else '; content delta %d files in %s' % (report['files'], (self.folder / 'assembled/content').name)
        )
        return '%s: %d B, sha256 %s%s' % (o.label, len(data), self.state['sha256'], delta)

    # -- upload --------------------------------------------------------------------------------
    def step_entry_free(self):
        default = self.slots('before')[2]['default']
        self.state['default'] = default
        if default == self.opts.id:
            raise StepFail('%s is the default entry: refused' % self.opts.id)
        names = self.names(LIBRARY)
        if default not in names:
            raise StepFail('ls firmware/: no %s (the default): the listing is wrong' % default)
        self.state['exists'] = self.opts.id in names
        if self.state['exists']:
            if not self.opts.replace:
                raise StepFail('firmware/%s exists: rerun with --replace, or --cleanup first' % self.opts.id)
            return 'firmware/%s exists: --replace, its info.txt %s' % (
                self.opts.id,
                self.remove(self.entry_path('info.txt')),
            )
        return 'firmware/%s absent; default %s' % (self.opts.id, default)

    def put(self, leaf, data):
        local = self.artifact(leaf)
        local.write_bytes(data)
        start = time.monotonic()
        self.ok(['put', local, self.entry_path(leaf), '--no-verify'])
        return time.monotonic() - start

    def step_put_os(self):
        if not self.state['exists']:
            self.ok(['mkdir', self.entry_path()])
        data = self.state['image']
        seconds = self.put('os.ldr', data)
        self.state['put_s'] = round(seconds, 1)
        return '%d B in %.1f s (%.1f KiB/s; device CRC checked)' % (
            len(data),
            seconds,
            len(data) / 1024.0 / max(seconds, 1e-6),
        )

    def step_put_info(self):
        info = info_for(self.state['image'], self.opts.label, self.opts.version)
        self.put('info.txt', info)
        self.state['info'] = info
        self.state['window'] = self.window_status()[2]  # right after the last write: still open
        return info.decode().strip().replace('\n', ' ')

    def step_ls_entry(self):
        listing = {e['name']: (e['type'], e['size']) for e in self.ls(self.entry_path())}
        want = {'os.ldr': ('file', len(self.state['image'])), 'info.txt': ('file', len(self.state['info']))}
        if listing != want:
            raise StepFail('ls firmware/%s: %s, expected %s' % (self.opts.id, listing, want))
        return 'os.ldr %d B, info.txt %d B' % (want['os.ldr'][1], want['info.txt'][1])

    def step_read_back(self):
        local = self.artifact('os.ldr')
        start = time.monotonic()
        self.ok(['get', self.entry_path('os.ldr'), local])
        seconds = time.monotonic() - start
        sha = hashlib.sha256(local.read_bytes()).hexdigest()
        if sha != self.state['sha256']:
            raise StepFail('read-back SHA-256 %s, expected %s' % (sha, self.state['sha256']))
        self.state['get_s'] = round(seconds, 1)
        n = len(self.state['image'])
        return 'whole file in %.1f s (%.1f KiB/s), SHA-256 equal' % (seconds, n / 1024.0 / max(seconds, 1e-6))

    def step_library(self):
        before = self.state['window']
        _, usb, server = self.window_status()
        if server['b_catalogs'] <= before['b_catalogs']:
            usb, server = self.settled(before, 'library')
        if server['b_window'] != 3:
            raise StepFail('op 9 answered %s (3 = it closed the open window)' % server['b_window'])
        catalog = self.menu_catalog('catalog.bin')
        item = [i for i in catalog['items'] if i['id'] == self.opts.id]
        if not item or not item[0]['usable'] or item[0]['name'] != self.opts.label[:47]:
            raise StepFail('menu catalog: %s is %s' % (self.opts.id, item[0] if item else 'missing'))
        if self.opts.id not in self.names(LIBRARY):
            raise StepFail('ls firmware/ does not list %s' % self.opts.id)
        default = self.slots('after')[2]['default']
        if default != self.state['default']:
            raise StepFail('default is %s, was %s' % (default, self.state['default']))
        return 'menu lists %s %r usable (item %d of %d); default %s unchanged' % (
            self.opts.id,
            item[0]['name'],
            catalog['items'].index(item[0]),
            catalog['count'],
            default,
        )

    # -- content -------------------------------------------------------------------------------
    def listing(self, path):
        """{name: (type, size)} of a device folder; None when it does not exist."""
        code, out, err = self.host(['ls', path, '--json'], dp.T_ANSWER)
        if code == dp.NOT_FOUND:
            return None
        if code:
            raise StepFail('ls %s: %s' % (path, dp.why(code, out, err)))
        return {e['name']: (e['type'], e['size']) for e in json.loads(out[out.index('[') :])}

    def get_saved(self, remote, local):
        self.ok(['get', remote, local])
        return hashlib.sha256(Path(local).read_bytes()).hexdigest()

    def step_content_save(self):
        source = self.opts.content_dir or self.folder / 'assembled/content'
        delta = content_delta(source)
        saved = self.folder / 'content-saved'
        saved.mkdir(exist_ok=True)
        catalog = '%s/%s' % (CONTENT, CATALOG_LEAF)
        try:
            sha = self.get_saved(catalog, saved / CATALOG_LEAF)
        except StepFail as error:
            raise StepFail('the device catalog could not be saved first (%s): no content written' % error)
        record = dict(
            folder=str(self.folder),
            catalog=dict(saved='content-saved/' + CATALOG_LEAF, sha256=sha),
            replaced=[],
            added_files=[],
            added_dirs=[],
            files=[rel for rel, _ in delta],
        )
        for rel, _ in delta:
            if rel == CATALOG_LEAF:
                continue
            parts, remote = rel.split('/'), '%s/%s' % (CONTENT, rel)
            for depth in range(1, len(parts)):  # the folders the put creates
                folder = '%s/%s' % (CONTENT, '/'.join(parts[:depth]))
                parent = (
                    self.listing(folder.rsplit('/', 1)[0])
                    if folder.rsplit('/', 1)[0] not in record['added_dirs']
                    else {}
                )
                if folder not in record['added_dirs'] and (parent is None or parts[depth - 1] not in parent):
                    record['added_dirs'].append(folder)
            folder = remote.rsplit('/', 1)[0]
            here = None if folder in record['added_dirs'] else self.listing(folder)
            if here and parts[-1] in here:
                local = saved / rel.replace('/', '__')
                record['replaced'].append(
                    dict(remote=remote, saved='content-saved/' + local.name, sha256=self.get_saved(remote, local))
                )
            else:
                record['added_files'].append(remote)
        self.state['content'] = record
        self.state['content_delta'] = delta
        return (
            'catalog saved (sha256 %s); delta of %d files from %s: %d new, %d replace a file (saved), '
            '%d new folders'
            % (sha, len(delta), source, len(record['added_files']), len(record['replaced']), len(record['added_dirs']))
        )

    def step_content_put(self):
        record, delta = self.state['content'], self.state['content_delta']
        for folder in record['added_dirs']:
            self.ok(['mkdir', folder])
        for rel, local in delta:  # the catalog last (content_delta)
            self.ok(['put', local, '%s/%s' % (CONTENT, rel)])
        return 'mkdir %d; put %d files (each read back identical), op1_factory.json last' % (
            len(record['added_dirs']),
            len(delta),
        )

    def step_dry_run_named(self):
        raise StepSkip(
            'no dry run of a named entry exists: CONTINUE mode 1 dry-runs the default only '
            '(docs/protocol/cmd-boot.md, 0x31)'
        )

    # -- boot once -----------------------------------------------------------------------------
    def slot_fields(self, tag):
        fields = self.slots(tag)[2]
        return fields['default'], fields.get('once', '')

    def step_boot_prepare(self):
        default, once = self.slot_fields('boot')
        if default == self.opts.id or once:
            raise StepFail('slots: default %s, once %r (expected another default, no once)' % (default, once))
        catalog = self.menu_catalog('boot-catalog.bin')
        item = [i for i in catalog['items'] if i['id'] == self.opts.id]
        if not item or not item[0]['usable']:
            raise StepFail('menu catalog: %s missing or not usable' % self.opts.id)
        self.state['default'] = default
        return 'default %s, no once; the menu lists %s usable' % (default, self.opts.id)

    def step_boot(self):
        self.state['booted'] = True
        self.ok(['boot', self.opts.id, '--yes-boot'])
        self.gone(dp.T_DRY, 'boot (library.boot)')
        return 'library.boot ok; USB gone: %s is booting once (default %s unchanged)' % (
            self.opts.id,
            self.state['default'],
        )

    # -- cleanup -------------------------------------------------------------------------------
    def step_cleanup_state(self):
        default, once = self.slot_fields('cleanup')
        expect = self.opts.expect_default or recorded_default(self.sessions, self.opts.sim)
        self.state['default'] = default
        if once:
            raise StepFail('once=%s is set' % once)
        if default == self.opts.id or (expect and default != expect):
            raise StepFail('default is %s, expected %s' % (default, expect or 'another entry'))
        return 'default %s %s, once empty' % (default, 'unchanged' if expect else '(no recorded default)')

    def step_cleanup_content(self):
        record = recorded_content(self.sessions, self.opts.sim)
        official = self.opts.official_catalog
        if record is None and official is None:
            raise StepSkip('no run recorded a content write; no --official-catalog')
        source = Path(official) if official else Path(record['folder']) / record['catalog']['saved']
        if not source.is_file():
            raise StepFail('%s missing: the catalog cannot be put back (--official-catalog PATH)' % source)
        want = hashlib.sha256(source.read_bytes()).hexdigest()
        catalog = '%s/%s' % (CONTENT, CATALOG_LEAF)
        self.ok(['put', source, catalog])
        back = self.get_saved(catalog, self.artifact('catalog-back.json'))
        if back != want:
            raise StepFail('%s read back: sha256 %s, expected %s' % (catalog, back, want))
        done = ['%s catalog back, read back sha256 %s' % ('official' if official else 'saved', want)]
        if record:
            for r in record['replaced']:
                self.ok(['put', Path(record['folder']) / r['saved'], r['remote']])
            removed = [self.remove(f) for f in record['added_files']]
            folders = [self.remove('--dir', d) for d in sorted(record['added_dirs'], key=len, reverse=True)]
            done.append(
                '%d replaced files put back; %d added files removed (%s); %d added folders removed'
                % (len(record['replaced']), len(removed), '/'.join(sorted(set(removed))) or '-', len(folders))
            )
        return '; '.join(done)

    def step_cleanup_remove(self):
        if self.opts.keep:
            raise StepSkip('--keep: firmware/%s kept' % self.opts.id)
        done = ['%s %s' % (leaf, self.remove(self.entry_path(leaf))) for leaf in ('os.ldr', 'info.txt')]
        done.append('folder %s' % self.remove('--dir', self.entry_path()))
        names = self.names(LIBRARY)
        if self.opts.id in names or self.state['default'] not in names:
            raise StepFail('ls firmware/: %s' % sorted(names))
        return '; '.join(done) + '; firmware/ lists %d entries, none %s' % (len(names), self.opts.id)

    def step_keepalive(self):
        if self.opts.sim:
            raise StepSkip('simulator: no keepalive.sh')
        if self.state.get('booted'):
            raise StepSkip('booted: the OS does not answer')
        return dp.Pass.step_keepalive(self)


def content_delta(source):
    """[(path under content/, local file)] of a content delta folder, the catalog last: only
    display/<id>.svg, presets/<id>/*.json and op1_factory.json (anything else: StepFail)."""
    source = Path(source)
    if not (source / CATALOG_LEAF).is_file():
        raise StepFail('no %s in the content delta %s' % (CATALOG_LEAF, source))
    out = []
    for path in sorted(p for p in source.rglob('*') if p.is_file()):
        rel = path.relative_to(source).as_posix()
        parts = rel.split('/')
        allowed = (
            rel == CATALOG_LEAF
            or (
                len(parts) == 2
                and parts[0] == 'display'
                and rel.endswith('.svg')
                and xyf_entry.ID.fullmatch(parts[1][:-4])
            )
            or (
                len(parts) == 3 and parts[0] == 'presets' and rel.endswith('.json') and xyf_entry.ID.fullmatch(parts[1])
            )
        )
        if not allowed:
            raise StepFail('content delta: %s is not display/<id>.svg, presets/<id>/*.json or %s' % (rel, CATALOG_LEAF))
        out.append((rel, path))
    return sorted(out, key=lambda f: f[0] == CATALOG_LEAF)


def recorded_content(sessions, sim):
    """The content record of the newest run that wrote content (its summary.json), or None."""
    kind = 'sim-mod-boot' if sim else 'mod-boot'
    for folder in sorted(Path(sessions).glob('*-%s-*' % kind), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            summary = json.loads((folder / 'summary.json').read_text())
        except OSError, ValueError:
            continue
        if summary.get('mode') == 'run' and summary.get('content'):
            return summary['content']
    return None


def recorded_default(sessions, sim):
    """default_at_start of the newest boot run's summary.json, or None."""
    kind = 'sim-mod-boot' if sim else 'mod-boot'
    for folder in sorted(Path(sessions).glob('*-%s-*' % kind), key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            summary = json.loads((folder / 'summary.json').read_text())
        except OSError, ValueError:
            continue
        if summary.get('mode') == 'run' and summary.get('default_at_start'):
            return summary['default_at_start']
    return None


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--plan', action='store_true', help='print the steps and exit; touches nothing')
    p.add_argument('--sim', metavar='HOST:PORT,CONTROL', help='the device simulator (keys not hosted: boot steps SKIP)')
    p.add_argument('--stages', help='comma list of assemble,upload,accept (default: all three)')
    p.add_argument('--mods', default='fm2op', help='comma list of machine ids (default fm2op)')
    p.add_argument('--image', type=Path, help='an os.ldr assembled earlier instead of the assemble step')
    p.add_argument('--packages', type=Path, default=PACKAGES)
    p.add_argument('--stock', type=Path, default=paths.STOCK_121 / 'os.ldr')
    p.add_argument('--catalog', type=Path, default=CATALOG)
    p.add_argument('--id', default=SCRATCH, help='scratch entry id (default %s)' % SCRATCH)
    p.add_argument('--replace', action='store_true', help='overwrite the scratch entry if it exists')
    p.add_argument(
        '--content',
        nargs='?',
        const='',
        metavar='DIR',
        help='after upload: save the device catalog, '
        'then write the content delta (DIR: one made earlier, needed with --image; default: the assembled one)',
    )
    p.add_argument(
        '--official-catalog',
        type=Path,
        metavar='PATH',
        help='with --cleanup: put this op1_factory.json back instead of the one the content run saved',
    )
    p.add_argument('--boot-once', action='store_true', help='after upload: boot the entry once (leaves XY Forever)')
    p.add_argument('--cleanup', action='store_true', help='alone: check default/once, remove the scratch entry')
    p.add_argument('--keep', action='store_true', help='with --cleanup: keep the entry')
    p.add_argument('--expect-default', metavar='ID', help='with --cleanup: the default to require')
    p.add_argument('--keep-evidence', action='store_true', help='--sim: keep the folder when everything passed')
    p.add_argument('--wait', type=float, default=dp.T_ANSWER, help='seconds the check waits for the device')
    return p.parse_args(argv)


def chosen_stages(o):
    if o.cleanup:
        return {'check', 'cleanup', 'end'}
    chosen = set(('assemble', 'upload', 'accept') if o.stages is None else (s.strip() for s in o.stages.split(',')))
    if chosen - {'assemble', 'upload', 'accept'}:
        raise ValueError('unknown stage(s) %s' % ', '.join(sorted(chosen - {'assemble', 'upload', 'accept'})))
    if 'upload' in chosen and 'assemble' not in chosen:
        raise ValueError('upload needs assemble (or --image) in the same run')
    if o.content is not None:
        if 'upload' not in chosen:
            raise ValueError('--content needs the upload stage')
        if o.content == '' and o.image:
            raise ValueError('--content with --image needs the content delta folder: --content DIR')
        chosen.add('content')
    if o.boot_once:
        if 'upload' not in chosen:
            raise ValueError('--boot-once needs the upload stage')
        chosen.add('boot-once')
    if chosen != {'assemble'}:
        chosen |= {'check', 'end'}
    return chosen


def main(argv=None, device=None, keepalive=None, sessions=None, sleep=time.sleep):
    o = parse_args(argv)
    o.machines = [m.strip() for m in o.mods.split(',') if m.strip()]
    o.content_dir = Path(o.content) if o.content else None
    o.label = 'Mods test: image' if o.image else 'Mods test: ' + ','.join(o.machines)
    if len(o.label) > 47:
        o.label = 'Mods test: %d machines' % len(o.machines)
    o.version = (
        'image'
        if o.image
        else package_records.read_values(o.packages / 'base').get('generation', 'mods')
        if package_records.local_name(o.packages / 'base')
        else 'mods'
    )
    try:
        chosen = chosen_stages(o)
        if not xyf_entry.ID.fullmatch(o.id):
            raise ValueError('id must be 1-31 of [a-z0-9_-]')
        unknown = [m for m in o.machines if not package_records.local_name(o.packages / m)]
        if 'assemble' in chosen and not o.image and unknown and (o.packages / 'base').is_dir():
            raise ValueError('no package for %s in %s' % (', '.join(unknown), o.packages))
        if o.sim:
            host.sim_address(o.sim)
            host.SIM = o.sim
    except ValueError as error:
        print('mod_boot: %s' % error, file=sys.stderr)
        return dp.EXIT_USAGE
    if o.plan:
        print('Mod boot plan: nothing is sent, nothing is written. Entry firmware/%s.' % o.id)
        for stage, steps in STAGES:
            print('%s (%s)' % (stage, 'on' if stage in chosen else 'off'))
            for name, text in steps:
                print('  %-15s %s' % (name, text))
        return dp.EXIT_PASS
    keepalive = keepalive or dp.Keepalive()
    run = ModBoot(device or dp.HostDevice(), o, keepalive, sleep)
    run.sessions = Path(sessions or dp.SESSIONS)
    stopped = [] if o.sim or 'check' not in chosen else keepalive.stop_running()
    started = datetime.now(UTC)
    rows, index, failed = [], 0, None
    kind = ('sim-' if o.sim else '') + 'mod-boot'
    for stage, steps in STAGES:
        for name, _ in steps:
            index += 1
            if stage not in chosen:
                continue
            if failed and stage != 'end':
                rows.append(
                    dict(stage=stage, step=name, result='SKIP', seconds=0.0, detail='after FAIL of %s' % failed)
                )
                continue
            if run.folder is None and stage != 'check':
                run.folder = dp.new_folder(run.sessions, kind)
                run.flush()
            print('[%s] %s/%s' % (time.strftime('%H:%M:%S'), stage, name), file=sys.stderr, flush=True)
            row = run.run_step(index, stage, name)
            if name == 'check' and row['result'] == 'FAIL' and run.no_answer:
                print(dp.SIM_NEED if o.sim else dp.NEED)
                return dp.EXIT_NEED
            rows.append(row)
            if row['result'] == 'FAIL':
                failed = '%s/%s' % (stage, name)
    code = dp.EXIT_FAIL if failed else dp.EXIT_PASS
    summary = dict(
        format='xyf-mod-boot/1',
        mode='cleanup' if o.cleanup else 'run',
        argv=list(argv or sys.argv[1:]),
        started_utc=started.isoformat(),
        finished_utc=datetime.now(UTC).isoformat(),
        entry=o.id,
        selection=o.machines,
        image_bytes=len(run.state.get('image', b'')) or None,
        image_sha256=run.state.get('sha256'),
        put_s=run.state.get('put_s'),
        get_s=run.state.get('get_s'),
        default_at_start=run.state.get('default'),
        booted=bool(run.state.get('booted')),
        content=run.state.get('content'),
        keepalives_stopped=stopped,
        steps=rows,
        exit_code=code,
    )
    (run.folder / 'summary.json').write_text(json.dumps(summary, indent=1) + '\n')
    booted = run.state.get('booted')
    keep = booted or run.state.get('content')  # the saved catalog lives in the folder
    print(dp.table(rows, dp.settle_folder(run.folder, o.sim, code == dp.EXIT_PASS and not keep, o.keep_evidence)))
    if booted:
        print(
            'BOOTING firmware/%s once (%s, %d B, sha256 %s).'
            % (o.id, o.label, len(run.state['image']), run.state['sha256'])
        )
        print(LOOK_FOR)
        print(RECOVERY % run.state['default'])
    return code


if __name__ == '__main__':
    sys.exit(main())
