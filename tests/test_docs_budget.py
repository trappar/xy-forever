"""Doc budgets, line width, links and the allowed docs/ files (docs/ORCHESTRATION.md, "Where things go").

Budgets are bytes, not lines: a line budget is met by writing longer lines. Every doc line has a width limit.
Over budget: split into a tree (an index plus leaves), do not squeeze.
"""
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / 'docs'
DECISIONS = DOCS / 'decisions'
STATUS = DOCS / 'status'            # local and gitignored: only its index is checked, when present

LEAF = 8000                         # a leaf of a tree, a decision record, a decision detail leaf (bytes)
INDEX = 4000                        # the index of a tree (bytes)
WIDTH = 160                         # longest line in any doc, outside code blocks and table rows

# Read by every agent, or by the orchestrator every sitting: (bytes, longest line).
FIRST_READS = {
    'AGENTS.md': (6500, 160),
    'ONBOARDING.md': (10000, 160),
    'docs/ORCHESTRATION.md': (9600, 190),
    'docs/STATUS.template.md': (2500, 120),
}
LOCAL_STATUS = 6000                 # docs/status/README.md; its leaves are not checked

# A big doc keeps its path as the index of a tree; its leaves sit in the folder. Until the folder exists,
# the doc's cap in CAPS applies.
TREES = {
    'docs/PROTOCOL.md': 'docs/protocol',
    'docs/ARCHITECTURE.md': 'docs/architecture',
    'docs/STORAGE.md': 'docs/storage',
    'docs/HARDWARE.md': 'docs/hardware',
}

# Read on demand (bytes). GOALS, OPERATING, SAFETY and GUIDE are over LEAF but outside #42's split.
CAPS = {
    'docs/ARCHITECTURE.md': 25600,
    'docs/GOALS.md': 14200,
    'docs/HARDWARE.md': 20200,
    'docs/HISTORY.md': 6600,
    'docs/OPERATING.md': 13800,
    'docs/SAFETY.md': 11500,
    'docs/STORAGE.md': 25000,
    'docs/PROTOCOL.md': 56000,      # generated from protocol/v1.json (scripts/protogen.py)
    'docs/GUIDE.md': 11500,         # for OP-XY owners and mod writers, not agents
    'docs/MODS-DEV.md': 5500,
}

ALLOWED = {
    'ARCHITECTURE.md', 'GOALS.md', 'GUIDE.md', 'HARDWARE.md', 'HISTORY.md', 'MODS-DEV.md', 'OPERATING.md',
    'ORCHESTRATION.md', 'PROTOCOL.md', 'SAFETY.md', 'STATUS.template.md', 'STORAGE.md',
}

# docs/decisions/: a record NNN-title.md, and its detail leaves NNN-title.<topic>.md, each linked from the record.
RECORD = re.compile(r'^(\d{3}-[a-z0-9-]+)\.md$')
DETAIL = re.compile(r'^(\d{3}-[a-z0-9-]+)\.[a-z0-9-]+\.md$')

# reflowed by #42 part B7 (files with a line over WIDTH outside code blocks and table rows)
UNWRAPPED = {
    'docs/HISTORY.md', 'docs/OPERATING.md',
    'docs/decisions/001-dead-code.md', 'docs/decisions/002-usb-function-driver.md',
    'docs/decisions/003-tfw-install.md', 'docs/decisions/004-protocol-v1.md', 'docs/decisions/005-mods.md',
    'docs/decisions/006-content.md', 'docs/decisions/007-stock-inventory.md',
    'docs/decisions/008-mod-assembly.md', 'docs/decisions/009-file-engine.md',
    'docs/decisions/010-package-model.md', 'docs/decisions/011-user-storage.md',
    'docs/decisions/012-boot-writer.md', 'docs/decisions/013-web-app.md', 'docs/decisions/014-extensions.md',
    'docs/decisions/015-companions.md', 'docs/decisions/016-guard-reset.md',
    'docs/decisions/017-ble-update-protocol.md', 'docs/decisions/018-catalog-entries.md',
    'docs/decisions/019-assembly-on-device.md', 'docs/decisions/020-code-space-and-stage2.md',
    'docs/decisions/021-restart-a-freeze.md', 'docs/decisions/022-lone-b-reset.md',
    'docs/decisions/023-knob-input.md', 'docs/decisions/024-safety-review.md',
    'docs/decisions/028-converging-saves.md', 'docs/decisions/032-run-time-mod-loading.md',
}

# Files whose relative links must resolve (docs/status/ is local and not checked).
LINKED = ['AGENTS.md', 'ONBOARDING.md', 'README.md']
BASE = {'docs/STATUS.template.md': STATUS}   # copied to docs/status/README.md: its links resolve from there

LINK = re.compile(r'\]\(([^)\s]+)(?:\s+"[^"]*")?\)')
HEADING = re.compile(r'^#{1,6}\s+(.*?)\s*#*\s*$')


def size(path):
    return len(path.read_bytes())


def longest(path):
    return max((len(l) for l in path.read_text(encoding='utf-8').splitlines()), default=0)


def prose(path):
    """(line number, line) for every line outside fenced code blocks."""
    fence = None
    for n, line in enumerate(path.read_text(encoding='utf-8').splitlines(), 1):
        mark = line.lstrip()[:3]
        if mark in ('```', '~~~'):
            fence = None if fence == mark else (fence or mark)
            continue
        if fence is None:
            yield n, line


def links(path):
    """Relative link targets (path, anchor) outside code; external links are skipped."""
    out = []
    for _, line in prose(path):
        for target in LINK.findall(re.sub(r'`[^`]*`', '', line)):
            if re.match(r'^[a-z][a-z0-9+.-]*:', target):
                continue
            file, _, anchor = target.partition('#')
            out.append((file, anchor))
    return out


def slugs(path):
    """GitHub heading anchors: lowercase, punctuation dropped except - and _, spaces to -, repeats numbered."""
    seen, out = {}, set()
    for _, line in prose(path):
        m = HEADING.match(line)
        if not m:
            continue
        text = re.sub(r'\[([^\]]*)\]\([^)]*\)', r'\1', m.group(1))
        slug = re.sub(r'[^\w\- ]', '', text.lower()).replace(' ', '-')
        n = seen.get(slug, 0)
        seen[slug] = n + 1
        out.add(slug if n == 0 else f'{slug}-{n}')
    return out


def docs():
    return sorted(p for p in DOCS.rglob('*.md') if STATUS not in p.parents)


def rel(path):
    return str(path.relative_to(ROOT))


class DocsBudget(unittest.TestCase):
    def check(self, path, budget, width=None):
        with self.subTest(doc=rel(path)):
            self.assertLessEqual(size(path), budget, f'{rel(path)} is over its budget: split it into a tree')
            if width:
                self.assertLessEqual(longest(path), width, f'{rel(path)} has a line over {width} characters')

    def test_first_reads(self):
        for name, (budget, width) in FIRST_READS.items():
            self.check(ROOT / name, budget, width)

    def test_local_status_index(self):
        index = STATUS / 'README.md'
        if not index.is_file():
            self.skipTest('no local docs/status/README.md')
        self.assertLessEqual(size(index), LOCAL_STATUS,
                             'your local docs/status/README.md is over 6 KB: move detail into leaf files beside it')

    def test_caps(self):
        for name, cap in CAPS.items():
            if name not in TREES or not (ROOT / TREES[name]).is_dir():
                self.check(ROOT / name, cap)

    def test_trees(self):
        for name, folder in TREES.items():
            index, folder = ROOT / name, ROOT / folder
            if not folder.is_dir():
                continue
            self.check(index, INDEX)
            leaves = sorted(folder.glob('*.md'))
            for leaf in leaves:
                self.check(leaf, LEAF)
            targets = [(index.parent / f).resolve() for f, _ in links(index) if f]
            with self.subTest(index=name):
                for leaf in leaves:
                    self.assertEqual(targets.count(leaf.resolve()), 1, f'{name} must link {rel(leaf)} exactly once')

    def test_decisions(self):
        for path in sorted(DECISIONS.iterdir()):
            name = path.name
            with self.subTest(decision=name):
                record, detail = RECORD.match(name), DETAIL.match(name)
                self.assertTrue(record or detail, f'{rel(path)}: name it NNN-title.md or NNN-title.<topic>.md')
                self.check(path, LEAF)
                if detail:
                    parent = DECISIONS / f'{detail.group(1)}.md'
                    self.assertTrue(parent.is_file(), f'{name} has no record {parent.name}')
                    self.assertIn(name, [Path(f).name for f, _ in links(parent)], f'{parent.name} must link {name}')

    def test_width(self):
        for path in docs():
            name = rel(path)
            long = [n for n, line in prose(path) if len(line) > WIDTH and not line.lstrip().startswith('|')]
            with self.subTest(doc=name):
                if name in UNWRAPPED:
                    self.assertTrue(long, f'{name} is reflowed: remove it from UNWRAPPED')
                else:
                    self.assertEqual(long, [], f'{name}: lines over {WIDTH} characters')
        for name in UNWRAPPED:
            self.assertTrue((ROOT / name).is_file(), f'UNWRAPPED names a missing doc {name}')

    def test_links(self):
        for path in docs() + [ROOT / n for n in LINKED]:
            broken = []
            for file, anchor in links(path):
                target = (BASE.get(rel(path), path.parent) / file) if file else path
                if not target.exists():
                    broken.append(f'{file}#{anchor}' if anchor else file)
                elif anchor and target.suffix == '.md' and anchor not in slugs(target):
                    broken.append(f'{file}#{anchor}')
            with self.subTest(doc=rel(path)):
                self.assertEqual(broken, [], f'{rel(path)} has broken links')

    def test_only_allowed_docs(self):
        folders = {ROOT / f for f in TREES.values()}
        extra = []
        for path in DOCS.rglob('*.md'):
            if path.parent == DECISIONS or path.parent in folders or STATUS in path.parents:
                continue
            if path.parent != DOCS or path.name not in ALLOWED:
                extra.append(rel(path))
        self.assertEqual(sorted(extra), [], 'docs/ holds files outside the allowed set')


if __name__ == '__main__':
    unittest.main()
