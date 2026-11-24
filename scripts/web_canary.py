#!/usr/bin/env python3
"""Canary XY Forever builds for the web app (#301): GitHub prereleases tagged canary-<commit>.

    uv run scripts/web_canary.py publish ARTIFACT --commit SHA
        CI on main (firmware.yml), after the product build: publishes ARTIFACT/os.ldr as prerelease canary-SHA
        with its pin, release.json, unless it is the os.ldr the current canary already has.
    uv run scripts/web_canary.py stage
        The Pages deploy (pages.yml): copies the newest eligible canary's os.ldr and pin into web/public/canary/,
        checked against each other, and the stable pin to web/public/release/release.json. No eligible canary:
        web/public/canary/ stays empty and the app offers Stable only.

A canary's pin is the stable pin's format (web_release.py) plus channel, commit and date (the commit's, UTC). It
is eligible when its tag names a commit on the deployed main that the stable release (tag v<version>) does not
contain, its pin matches its tag and its os.ldr, and its image is not the stable one. Newest: the latest created.
The firmware workflow builds without stock files; this script publishes only os.ldr and the pin it writes.
"""

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

import web_release

from xy_forever import release

ROOT = Path(__file__).resolve().parents[1]
CANARY = ROOT / 'web/public/canary'
STABLE_PIN = ROOT / 'web/public/release/release.json'
TAG = re.compile(r'canary-([0-9a-f]{40})')
PIN = 'release.json'


class GitHub:
    """The gh and git calls this script makes (tests pass a fake)."""

    def __init__(self, cwd=ROOT):
        self.cwd = cwd

    def _run(self, *args):
        return subprocess.run(args, cwd=self.cwd, check=True, capture_output=True, text=True).stdout

    def canaries(self):
        """Published canary prereleases, newest first: [tag]."""
        found = json.loads(
            self._run(
                'gh',
                'release',
                'list',
                '--exclude-drafts',
                '--limit',
                '200',
                '--json',
                'tagName,isPrerelease,createdAt',
            )
        )
        found = [r for r in found if r['isPrerelease'] and TAG.fullmatch(r['tagName'])]
        return [r['tagName'] for r in sorted(found, key=lambda r: r['createdAt'], reverse=True)]

    def download(self, tag, name, folder):
        self._run('gh', 'release', 'download', tag, '--pattern', name, '--dir', str(folder), '--clobber')
        return Path(folder) / name

    def create(self, tag, commit, title, notes, files):
        self._run(
            'gh', 'release', 'create', tag, '--prerelease', '--latest=false', '--target', commit,
            '--title', title, '--notes', notes, *map(str, files),
        )  # fmt: skip

    def contains(self, ancestor, commit):
        """Whether commit's history holds ancestor (both on this clone)."""
        git = subprocess.run(['git', 'merge-base', '--is-ancestor', ancestor, commit], cwd=self.cwd)
        if git.returncode > 1:
            raise RuntimeError('git merge-base failed for %s and %s' % (ancestor, commit))
        return git.returncode == 0

    def commit_date(self, commit):
        stamp = int(self._run('git', 'show', '-s', '--format=%ct', commit))
        return datetime.fromtimestamp(stamp, UTC).strftime('%Y-%m-%dT%H:%M:%SZ')


def canary_pin(data, commit, date):
    return dict(web_release.pin_of(data), channel='canary', commit=commit, date=date)


def why_not(pin, data, tag):
    """None when a canary's pin matches its tag and its os.ldr, else why not."""
    if pin.get('channel') != 'canary' or 'canary-%s' % pin.get('commit') != tag:
        return 'its pin names another channel or commit'
    if not re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', str(pin.get('date'))):
        return 'its pin has no date'
    actual = web_release.pin_of(data)
    if any(pin.get(key) != actual[key] for key in ('schema', 'sha256', 'image', 'bytes')):
        return 'its os.ldr is not the one its pin names'
    return None


def built(artifact):
    """The CI build's os.ldr when its manifest names it (a verified product build of this checkout's version)."""
    data = (Path(artifact) / 'os.ldr').read_bytes()
    manifest = json.loads((Path(artifact) / 'manifest.json').read_text())
    if manifest.get('mode') != 'xyf-product' or manifest.get('candidate_sha256') != hashlib.sha256(data).hexdigest():
        raise ValueError('%s/os.ldr is not the product build its manifest names' % artifact)
    if manifest.get('version') != release.VERSION:
        raise ValueError('the build is release %s, this checkout %s' % (manifest.get('version'), release.VERSION))
    return data


def publish(artifact, commit, gh):
    """Publishes the build as canary-<commit> unless the current canary has the same os.ldr; returns its pin."""
    data = built(artifact)
    pin = canary_pin(data, commit, gh.commit_date(commit))
    tag = 'canary-%s' % commit
    with tempfile.TemporaryDirectory() as tmp:
        current = gh.canaries()
        if current:
            previous = json.loads(gh.download(current[0], PIN, tmp).read_text())
            if previous.get('sha256') == pin['sha256']:
                return None, 'unchanged: %s already has image %s' % (current[0], pin['image'])
        (Path(tmp) / 'os.ldr').write_bytes(data)
        (Path(tmp) / PIN).write_text(json.dumps(pin, indent=2) + '\n')
        notes = 'Experimental XY Forever %s build of %s (image %s, SHA-256 %s). Offered as Canary in the web app.' % (
            pin['version'], commit, pin['image'], pin['sha256'],
        )  # fmt: skip
        title = 'Canary %s (%s)' % (commit[:7], pin['date'][:10])
        gh.create(tag, commit, title, notes, [Path(tmp) / 'os.ldr', Path(tmp) / PIN])
    return pin, 'published %s: image %s, %d bytes' % (tag, pin['image'], pin['bytes'])


def stage(stable, gh, head='HEAD', folder=CANARY, log=print):
    """Copies the newest eligible canary into folder and returns its pin; None (folder emptied) when there is none."""
    folder.mkdir(parents=True, exist_ok=True)
    for name in ('os.ldr', PIN):
        (folder / name).unlink(missing_ok=True)
    stable_tag = 'v%s' % stable['version']
    with tempfile.TemporaryDirectory() as tmp:
        for tag in gh.canaries():
            commit = TAG.fullmatch(tag).group(1)
            if not gh.contains(commit, head):
                log('%s: not on the deployed main, skipped' % tag)
                continue
            if gh.contains(commit, stable_tag):
                log('%s: the stable release %s contains it: no newer canary' % (tag, stable_tag))
                return None
            try:
                pin = json.loads(gh.download(tag, PIN, tmp).read_text())
                data = gh.download(tag, 'os.ldr', tmp).read_bytes()
            except (subprocess.CalledProcessError, OSError, ValueError) as error:
                log('%s: its assets could not be read (%s), skipped' % (tag, error))
                continue
            why = why_not(pin, data, tag)
            if why:
                log('%s: %s, skipped' % (tag, why))
                continue
            if pin['image'] == stable['image']:
                log('%s: the stable image %s: no newer canary' % (tag, stable['image']))
                return None
            (folder / 'os.ldr').write_bytes(data)
            (folder / PIN).write_text(json.dumps(pin, indent=2) + '\n')
            return pin
    log('no canary on the deployed main')
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    p = commands.add_parser('publish')
    p.add_argument('artifact', type=Path, help="the firmware workflow's artifact: os.ldr, manifest.json")
    p.add_argument('--commit', required=True, help='the full commit SHA it was built from')
    commands.add_parser('stage')
    args = parser.parse_args()
    gh = GitHub()
    if args.command == 'publish':
        if not re.fullmatch(r'[0-9a-f]{40}', args.commit):
            parser.error('--commit takes a full commit SHA')
        try:
            _, said = publish(args.artifact, args.commit, gh)
        except ValueError as error:
            print('refused: %s' % error)
            return 1
        print(said)
        return 0
    why = web_release.check()
    if why:
        print('refused: %s' % why)
        return 1
    stable = json.loads(web_release.PIN.read_text())
    STABLE_PIN.write_text(json.dumps(stable, indent=2) + '\n')
    pin = stage(stable, gh)
    print('canary: %s' % ('%s (image %s, %s)' % (pin['commit'], pin['image'], pin['date']) if pin else 'none'))
    return 0


if __name__ == '__main__':
    sys.exit(main())
