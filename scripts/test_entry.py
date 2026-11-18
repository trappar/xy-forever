#!/usr/bin/env python3
"""Test entry: a host-built Open-XY os.ldr into XY Forever's library as an assembled mod set the current
firmware accepts (a TEST path only: the OP-XY assembles mod firmware itself; host assembly is never a feature).

The entry is what native/mod_job writes (docs/storage/mods-assembly.md, "The assembled entry"; host twin
modpatch.entry_files): firmware/<id>/ with the candidate's content/... (op1_factory.json, display/, presets/),
info.txt (type=firmware name kind=open-xy bytes crc32 sha256 needs_os
generation profile machines), files.sha256 over every other file (sorted bytewise) and os.ldr. needs_os is the sha256=
of firmware/<--stock-entry>/info.txt (the stock os.ldr the image was built from); generation and profile are
those of firmware/<--like>/info.txt (the device's own Open-XY entry).
The candidate's xy-forever/loaded/<id>/... files (runtime-loaded objects) go to /yaffs2/xy-forever/loaded/<id>/.

Modes (each a separate run; the first FAIL stops, exit 1; usage exit 2; with --sim the evidence folder is
removed when the run passed, unless --keep):
  upload (default) completes or replaces firmware/<id>/: info.txt removed first, files the entry does not
                   hold removed, os.ldr kept when its size and the old info.txt's sha256= match, info.txt
                   written last; B's catalog then lists <id> as "content not installed" (expected).
  --boot           set-default <id> (library.set_default: B's content switch makes /yaffs2/content/ match
                   the entry and writes content-state.txt `state=installed entry=<id>`, then default=<id>),
                   then boot <id> once (library.boot). USB goes.
  --cleanup        refused while <id> is the default or once, or content-state.txt names it (first:
                   `xyf_host.py set-default <previous default> --yes-write`); removes firmware/<id>/ and
                   loaded/<id>/.
This script writes only firmware/<id>/ and loaded/<id>/; never /yaffs2/content/, content-state.txt,
config.txt or the state slots (only the device's set-default does, in --boot).
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import time
import zlib

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))
import device_pass as dp                                    # noqa: E402
import xyf_host as host                                     # noqa: E402
from xy_forever import modpatch, xyf_entry                  # noqa: E402

StepFail = dp.StepFail
XYF = '/yaffs2/xy-forever'
RECORD = XYF + '/content-state.txt'
T_SWITCH = 300.0               # set-default with the content switch (a job; xyf_host waits for it)


def entry_files(image, content, name, needs_os, generation, profile, machines=None):
    """{path: bytes} of the entry, as modpatch.entry_files (the device job's host twin) builds one."""
    files = {'content/' + p: b for p, b in content.items()}
    files['os.ldr'] = image
    files['info.txt'] = xyf_entry.manifest_text(dict(
        type='firmware', name=name, kind='open-xy', bytes=len(image), crc32='%08x' % (zlib.crc32(image) & 0xffffffff),
        sha256=hashlib.sha256(image).hexdigest(), needs_os=needs_os, generation=generation,
        profile=profile, **({'machines': machines} if machines is not None else {}))).encode()
    files['files.sha256'] = modpatch.list_text(files).encode()
    xyf_entry.read_files_sha256(files['files.sha256'])
    xyf_entry.parse_manifest(files['info.txt'])
    return files


def candidate(folder, entry_id):
    """(os.ldr, {content path: bytes}, {loaded/<id>/... path: bytes}) of a build's candidate folder."""
    folder = Path(folder)
    content = modpatch.tree(folder / 'content')
    extras = modpatch.tree(folder / 'xy-forever') if (folder / 'xy-forever').is_dir() else {}
    stray = [p for p in extras if not p.startswith('loaded/%s/' % entry_id)]
    if stray:
        raise ValueError('candidate xy-forever/ holds files outside loaded/%s/: %s' % (entry_id, ', '.join(stray)))
    if 'op1_factory.json' not in content:
        raise ValueError('%s/content has no op1_factory.json' % folder)
    return (folder / 'os.ldr').read_bytes(), content, extras


def values(text):
    return dict(l.split('=', 1) for l in text.decode('ascii', 'replace').splitlines() if '=' in l)


class TestEntry(dp.Pass):
    allow_boot = True              # --boot: library.boot of the test entry

    def say(self, text):
        print('  ' + text, flush=True)

    def entry(self, leaf=''):
        return '%s/firmware/%s%s' % (XYF, self.opts.id, '/' + leaf if leaf else '')

    def walk(self, path):
        """(files {relative path: size}, folders [relative path]) under a device folder; None if absent."""
        code, out, err = self.host(['ls', path, '--json'], dp.T_ANSWER)
        if code == dp.NOT_FOUND:
            return None
        if code:
            raise StepFail('ls %s: %s' % (path, dp.why(code, out, err)))
        files, folders = {}, []
        for e in json.loads(out[out.index('['):]):
            if e['type'] == 'dir':
                sub = self.walk('%s/%s' % (path, e['name']))
                folders += [e['name']] + ['%s/%s' % (e['name'], d) for d in sub[1]]
                files.update({'%s/%s' % (e['name'], f): s for f, s in sub[0].items()})
            else:
                files[e['name']] = e['size']
        return files, folders

    def remove_tree(self, path):
        found = self.walk(path)
        if found is None:
            return 0
        for leaf in found[0]:
            self.remove('%s/%s' % (path, leaf))
        for folder in sorted(found[1], key=len, reverse=True):
            self.remove('--dir', '%s/%s' % (path, folder))
        self.remove('--dir', path)
        return len(found[0])

    def default_once(self):
        fields = self.slots('slots')[2]
        return fields['default'], fields.get('once', '')

    def record(self):
        data = self.read(RECORD, 'content-state.txt')
        return values(data or b'')

    def put_tree(self, base, files, local):
        """mkdir what is missing, then put each file (in the given order) under base."""
        have = self.walk(base)
        folders = set(have[1]) if have else set()
        if have is None:
            self.ok(['mkdir', base])
        for leaf in files:
            parts = leaf.split('/')[:-1]
            for n in range(1, len(parts) + 1):
                folder = '/'.join(parts[:n])
                if folder not in folders:
                    self.ok(['mkdir', '%s/%s' % (base, folder)])
                    folders.add(folder)
            self.ok(['put', local / leaf, '%s/%s' % (base, leaf), '--no-verify'], 30.0)

    # -- upload --------------------------------------------------------------------------------
    def upload(self):
        o = self.opts
        image, content, extras = candidate(o.candidate, o.id)
        default, once = self.default_once()
        if o.id in (default, once):
            raise StepFail('%s is the default or once entry: refused' % o.id)
        stock = values(self.read('%s/firmware/%s/info.txt' % (XYF, o.stock_entry), 'stock-info.txt') or b'')
        like = values(self.read('%s/firmware/%s/info.txt' % (XYF, o.like), 'like-info.txt') or b'')
        if not stock or not {'generation', 'profile'} <= set(like):
            raise StepFail('firmware/%s/info.txt or firmware/%s/info.txt (generation, profile) missing'
                           % (o.stock_entry, o.like))
        if 'sha256' not in stock and like.get('needs_os'):              # an official entry's info.txt may lack it
            stock['sha256'] = like['needs_os']
            self.say('note: firmware/%s/info.txt has no sha256=; needs_os taken from firmware/%s/info.txt'
                     % (o.stock_entry, o.like))
        elif 'sha256' not in stock:
            local = self.artifact('stock-os.ldr')
            self.ok(['get', '%s/firmware/%s/os.ldr' % (XYF, o.stock_entry), local], 60.0)
            stock['sha256'] = hashlib.sha256(local.read_bytes()).hexdigest()
            self.say('needs_os: SHA-256 of firmware/%s/os.ldr read back' % o.stock_entry)
        files = entry_files(image, content, o.name, stock['sha256'], like['generation'], like['profile'])
        local = self.folder / 'entry'
        for path, data in files.items():
            (local / path).parent.mkdir(parents=True, exist_ok=True)
            (local / path).write_bytes(data)
        self.say('entry: %d files (%d content), os.ldr %d B sha256 %s; needs_os %s (%s), generation %s'
                 % (len(files), len(content), len(image), hashlib.sha256(image).hexdigest(), stock['sha256'],
                    o.stock_entry, like['generation']))
        old = self.walk(self.entry())
        keep_os = False
        if old is not None:
            old_info = values(self.read(self.entry('info.txt'), 'old-info.txt') or b'')
            keep_os = old[0].get('os.ldr') == len(image) and old_info.get('sha256') == hashlib.sha256(image).hexdigest()
            if 'info.txt' in old[0]:
                self.remove(self.entry('info.txt'))            # first: never a complete-looking half entry
            stale = [f for f in old[0] if f not in files and f != 'info.txt']
            for leaf in stale:
                self.remove(self.entry(leaf))
            for folder in sorted(old[1], key=len, reverse=True):
                if not any(f.startswith(folder + '/') for f in files):
                    self.remove('--dir', self.entry(folder))
            self.say('firmware/%s existed (%d files): %d stale removed, os.ldr %s'
                     % (o.id, len(old[0]), len(stale), 'kept (same size and sha256)' if keep_os else 'replaced'))
        order = sorted(f for f in files if f not in ('os.ldr', 'info.txt')) + ([] if keep_os else ['os.ldr'])
        self.put_tree(self.entry(), order + ['info.txt'], local)
        if extras:
            for path, data in extras.items():
                (self.folder / 'extra' / path).parent.mkdir(parents=True, exist_ok=True)
                (self.folder / 'extra' / path).write_bytes(data)
            self.put_tree(XYF + '/loaded', [p[len('loaded/'):] for p in sorted(extras)], self.folder / 'extra/loaded')
            self.say('loaded/%s/: %d files' % (o.id, len(extras)))
        window = self.window_status()[2]
        listed = self.walk(self.entry())[0]
        want = {p: len(b) for p, b in files.items()}
        if listed != want:
            raise StepFail('ls firmware/%s: %s, expected %s' % (o.id, listed, want))
        self.settled(window, 'upload')
        item = [i for i in self.menu_catalog('catalog.bin')['items'] if i['id'] == o.id]
        if not item or not item[0]['content_missing'] or item[0]['name'] != o.name:
            raise StepFail('menu catalog: %s is %s (expected listed, content not installed)'
                           % (o.id, item[0] if item else 'missing'))
        self.say('ls matches; catalog lists %s %r, content not installed (installed by --boot); default %s'
                 % (o.id, o.name, default))

    # -- boot ----------------------------------------------------------------------------------
    def boot(self):
        o = self.opts
        default, once = self.default_once()
        if once:
            raise StepFail('once=%s is set' % once)
        if self.walk(self.entry()) is None:
            raise StepFail('firmware/%s absent: upload first' % o.id)
        self.say('previous default %s; content state %s' % (default, self.record().get('state', 'official')))
        self.ok(['set-default', o.id, '--yes-write'], T_SWITCH)
        record = self.record()
        if record.get('state') != 'installed' or record.get('entry') != o.id or self.default_once()[0] != o.id:
            raise StepFail('after set-default: record %s, default %s' % (record, self.default_once()[0]))
        self.say('content switch done: content-state.txt installed entry=%s; default=%s' % (o.id, o.id))
        self.ok(['boot', o.id, '--yes-boot'])
        self.gone(dp.T_DRY, 'boot (library.boot)')
        self.say('library.boot ok; USB gone: %s is booting. Back: hold PLAY at power-on, then '
                 '`xyf_host.py set-default %s --yes-write` and `scripts/test_entry.py --id %s --cleanup`'
                 % (o.id, default, o.id))

    # -- cleanup -------------------------------------------------------------------------------
    def cleanup(self):
        o = self.opts
        default, once = self.default_once()
        record = self.record()
        if o.id in (default, once) or o.id in (record.get('entry'), record.get('target')):
            raise StepFail('default %s, once %r, content state %s: set another default first' % (default, once, record))
        n = self.remove_tree(self.entry())
        m = self.remove_tree('%s/loaded/%s' % (XYF, o.id))
        if o.id in self.names(XYF + '/firmware'):
            raise StepFail('firmware/%s still listed' % o.id)
        self.say('firmware/%s removed (%d files), loaded/%s removed (%d files); default %s' % (o.id, n, o.id, m, default))


def parse_args(argv):
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--id', default='loader-test')
    p.add_argument('--name', default='Loader test', help='display name (at most 47 bytes)')
    p.add_argument('--candidate', type=Path, help='upload: os.ldr, content/, xy-forever/loaded/ (a build folder)')
    p.add_argument('--stock-entry', default='opxy-121-stock', help='its info.txt sha256= becomes needs_os')
    p.add_argument('--like', default='mods-a', help='the Open-XY entry whose generation and profile are used')
    mode = p.add_mutually_exclusive_group()
    mode.add_argument('--boot', action='store_true', help='set-default <id> (content switch), then boot it once')
    mode.add_argument('--cleanup', action='store_true', help='remove the entry (not while default or installed)')
    p.add_argument('--sim', metavar='HOST:PORT')
    p.add_argument('--keep', action='store_true', help='with --sim: keep the evidence folder when it passed')
    o = p.parse_args(argv)
    if not xyf_entry.ID.fullmatch(o.id) or len(o.name.encode()) > 47:
        p.error('id must be 1-31 of [a-z0-9_-], name at most 47 bytes')
    if not (o.boot or o.cleanup) and not o.candidate:
        p.error('an upload needs --candidate')
    return o


def main(argv=None, device=None, sleep=time.sleep, folder=None):
    o = parse_args(argv)
    if o.sim:
        host.sim_address(o.sim)
        host.SIM = o.sim
    run = TestEntry(device or dp.HostDevice(), o, None, sleep)
    run.folder = Path(folder) if folder else (Path(tempfile.mkdtemp(prefix='xyf-test-entry-')) if o.sim else
                                              dp.new_folder(dp.SESSIONS, 'test-entry'))
    mode = 'boot' if o.boot else 'cleanup' if o.cleanup else 'upload'
    print('test_entry %s firmware/%s (evidence %s)' % (mode, o.id, run.folder), flush=True)
    code = 0
    try:
        getattr(run, mode)()
        print('PASS', flush=True)
    except (StepFail, ValueError, OSError) as error:
        print('FAIL: %s' % error, flush=True)
        code = 1
    (run.folder / 'host-log.txt').write_text(''.join(run.log))
    if not folder:
        print('evidence: %s' % dp.settle_folder(run.folder, o.sim, code == 0, o.keep), flush=True)
    return code


if __name__ == '__main__':
    sys.exit(main())
