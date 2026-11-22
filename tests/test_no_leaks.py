"""No personal data in tracked files.

Scans every file `git ls-files` lists (binaries skipped) and fails, naming file:line, on:

- email addresses, except EMAILS_OK (generic) and any address at an example domain;
- home paths `/Users/<name>`, `/home/<name>`, `C:\\Users\\<name>` (placeholders such as `/Users/<you>/`,
  `~/` and `/Users/Shared` are fine);
- the maintainer's evidence: `xy-forever/{sessions,analysis,reports}/<name>` for a concrete <name> other than
  the folders the tools themselves own (TOOL_FOLDERS; `<date>-...` placeholders are fine), and `keys/<file>`
  other than the key files the code reads (KEY_FILES);
- key-shaped hex: a run of exactly 32 or 64 hex digits, unless it is a known pin (every run in PIN_FILES and
  in protocol/v1.json's "golden" and "streams" vectors), the line labels it (DIGEST_LABEL: SHA-256, CRC, digest, hash,
  vector, ...; a run labelled on one line is known on every line), or the line carries the marker `leak-ok`;
- the private regexes in the gitignored xyf.local.json, key "leak_patterns" (a list of regexes: your name,
  emails, hostnames), so they never have to be written into the repository (see `scripts/setup.py doctor`).

A line ending in a `leak-ok` comment is exempt from every rule; use it only with a reason on the same line.
"""

import json
import re
import subprocess
import tempfile
import unittest
from pathlib import Path

import pytest

from xy_forever import paths

ROOT = Path(__file__).resolve().parents[1]

MARKER = 'leak-ok'
EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+\.)+[A-Za-z]{2,}')
EMAILS_OK = {
    'noreply@anthropic.com',  # commit attribution
    'git@github.com',  # GitHub's SSH clone user
    'vincent.rijmen@esat.kuleuven.ac.be',  # third_party/rijndael-fst: the authors in its public-domain header
    'antoon.bosselaers@esat.kuleuven.ac.be',
    'paulo.barreto@terra.com.br',
}
EXAMPLE_DOMAINS = re.compile(r'@(.+\.)?example\.(com|org|net)$', re.I)
HOME = re.compile(r'(?:/Users|/home)/(?!<|Shared\b)[A-Za-z0-9._-]+|[A-Za-z]:\\+Users\\+(?!<)[A-Za-z0-9._-]+')
EVIDENCE = re.compile(r'xy-forever/(sessions|analysis|reports)/([^\s/`\'"<>)]+)')
TOOL_FOLDERS = {
    'simulator',  # xy_forever/sim.py's state and stock image
    'neutral',  # scripts/neutral.py's worktrees
    'bootloader-121-listing',
}  # paths.LISTING_121, the re/ tools' input
KEY_FILE = re.compile(r'\bkeys/([A-Za-z0-9._-]+\.[A-Za-z0-9]+)')
KEY_FILES = {'opxy-update-keys.json', 'opxy-tfw-public-key.json'}  # read by tfw_extract, xyf_package, sim
HEX = re.compile(r'(?<![0-9A-Fa-f])(?:[0-9A-Fa-f]{64}|[0-9A-Fa-f]{32})(?![0-9A-Fa-f])')
DIGEST_LABEL = re.compile(r'sha-?(1|256|512)|crc|digest|hash|vector|checksum|md5', re.I)
PIN_FILES = ('stock.json', 'toolchains.json')


def tracked_texts(root=ROOT):
    """{relative path: text} of every tracked file that decodes as UTF-8 and has no NUL byte."""
    names = subprocess.run(['git', 'ls-files', '-z'], cwd=root, capture_output=True, check=True).stdout
    out = {}
    for name in filter(None, names.decode().split('\0')):
        try:
            data = (Path(root) / name).read_bytes()
        except OSError:  # deleted in the working tree
            continue
        if b'\0' in data:
            continue
        try:
            out[name] = data.decode('utf-8')
        except UnicodeDecodeError:
            continue
    return out


def known_hex(texts):
    """Pins and vectors (lower case): every run in PIN_FILES and v1.json's golden and stream vectors, and every run
    on a line that labels it as a digest."""
    known = set()
    for name in PIN_FILES:
        known.update(HEX.findall(texts.get(name, '')))
    if 'protocol/v1.json' in texts:
        spec = json.loads(texts['protocol/v1.json'])
        known.update(HEX.findall(json.dumps([spec.get('golden'), spec.get('streams')])))
    for text in texts.values():
        for line in text.splitlines():
            if DIGEST_LABEL.search(line):
                known.update(HEX.findall(line))
    return {h.lower() for h in known}


def find_leaks(texts, private_patterns=()):
    """['file:line: rule: match'] for every hit."""
    known = known_hex(texts)
    private = [re.compile(p) for p in private_patterns]
    hits = []
    for name, text in sorted(texts.items()):
        for number, line in enumerate(text.splitlines(), 1):
            if MARKER in line:
                continue
            found = []
            found += [
                ('email', m.group(0))
                for m in EMAIL.finditer(line)
                if m.group(0) not in EMAILS_OK and not EXAMPLE_DOMAINS.search(m.group(0))
            ]
            found += [('home path', m.group(0)) for m in HOME.finditer(line)]
            found += [('evidence path', m.group(0)) for m in EVIDENCE.finditer(line) if m.group(2) not in TOOL_FOLDERS]
            found += [('key file', m.group(0)) for m in KEY_FILE.finditer(line) if m.group(1) not in KEY_FILES]
            found += [('key-shaped hex', h) for h in HEX.findall(line) if h.lower() not in known]
            found += [('private pattern', m.group(0)) for p in private for m in p.finditer(line)]
            hits += ['%s:%d: %s: %s' % (name, number, rule, match) for rule, match in found]
    return hits


@pytest.mark.gate
class NoLeaksTest(unittest.TestCase):
    def test_tracked_files_have_no_personal_data(self):
        try:
            texts = tracked_texts()
        except OSError, subprocess.CalledProcessError:
            self.skipTest('not a git checkout')
        self.assertGreater(len(texts), 100)
        hits = find_leaks(texts, paths.leak_patterns())
        self.assertEqual(hits, [], '\n'.join(hits))

    def test_rules(self):
        base = {'stock.json': '{"os": "%s"}\n' % ('ab' * 32), 'protocol/v1.json': '{"golden": []}\n'}
        clean = [
            'commit trailer Co-Authored-By: Claude <noreply@anthropic.com>',
            'mail dev@example.com or ops@lists.example.org',
            'home: /Users/<you>/private, ~/private, /Users/Shared/x, C:\\Users\\<you>\\x',
            'evidence: ../private/xy-forever/sessions/<date>-device-pass-<n>/ and sessions/simulator/',
            'listing: private/xy-forever/analysis/bootloader-121-listing/FINDINGS.md',
            'keys: ../private/keys/opxy-update-keys.json; B = screen/keys/USB/PSoC',
            'pin: ' + 'ab' * 32,
            'SHA-256 `' + 'cd' * 32 + '`',
            'needs_os=' + 'cd' * 32,  # labelled on the line above
            'git commit ' + 'e' * 40,  # 40 hex: a commit, not key-shaped
            'secret = "%s"  # leak-ok: synthetic test key' % ('5a' * 16),
        ]
        self.assertEqual(find_leaks(dict(base, **{'a.txt': '\n'.join(clean)})), [])
        dirty = [  # planted inputs, each line marked for the scan
            ('someone@gmail.com', 'email'),  # leak-ok
            ('/Users/someone/x', 'home path'),  # leak-ok
            ('/home/someone/x', 'home path'),  # leak-ok
            ('C:\\Users\\someone\\x', 'home path'),  # leak-ok
            ('private/xy-forever/sessions/2026-11-16-audit/AUDIT.md', 'evidence path'),  # leak-ok
            ('private/xy-forever/analysis/xy-forever-usb-001', 'evidence path'),  # leak-ok
            ('private/xy-forever/reports/test-031/', 'evidence path'),  # leak-ok
            ('private/keys/other-key.json', 'key file'),  # leak-ok
            ('key = ' + '0123456789abcdef' * 2, 'key-shaped hex'),
            ('key = ' + '0123456789ABCDEF' * 4, 'key-shaped hex'),
            ('signed by Jane Q. Hacker', 'private pattern'),
        ]
        for line, rule in dirty:
            with self.subTest(line):
                hits = find_leaks(dict(base, **{'a.txt': 'ok\n' + line + '\n'}), [r'Jane Q\.? Hacker'])
                self.assertEqual(len(hits), 1, hits)
                self.assertTrue(hits[0].startswith('a.txt:2: %s: ' % rule), hits)

    def test_leak_patterns_come_from_the_local_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / 'xyf.local.json'
            self.assertEqual(paths.leak_patterns(config), [])
            config.write_text(json.dumps({'private': tmp, 'leak_patterns': ['Jane Q\\.? Hacker', 'jqh@']}))
            self.assertEqual(paths.leak_patterns(config), ['Jane Q\\.? Hacker', 'jqh@'])
            hits = find_leaks({'a.txt': 'by Jane Q Hacker\n'}, paths.leak_patterns(config))
            self.assertEqual(hits, ['a.txt:1: private pattern: Jane Q Hacker'])
            config.write_text(json.dumps({'leak_patterns': 'Jane'}))
            with self.assertRaises(ValueError):
                paths.leak_patterns(config)


if __name__ == '__main__':
    unittest.main()
