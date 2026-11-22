"""Doc budgets and links (docs/ORCHESTRATION.md, "Where things go").

Budgets are bytes, not lines: a line budget is met by writing longer lines.
Over budget: split into a tree (an index plus leaves), do not squeeze.
"""

import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DOCS = ROOT / 'docs'

LEAF = 8000  # a current-reference leaf, and any other doc without a budget below (bytes)
INDEX = 4000  # the index of a tree (bytes)

# Read by every agent, or by the orchestrator every sitting (bytes).
FIRST_READS = {
    'AGENTS.md': 5000,
    'ONBOARDING.md': 5000,
    'docs/ORCHESTRATION.md': 5000,
    'docs/STATUS.template.md': 1800,
}

# A big doc keeps its path as the index of a tree (INDEX); its leaves sit in the folder (LEAF each).
TREES = {
    'docs/PROTOCOL.md': 'docs/protocol',
    'docs/ARCHITECTURE.md': 'docs/architecture',
    'docs/architecture/boot.md': 'docs/architecture/boot',
    'docs/STORAGE.md': 'docs/storage',
    'docs/storage/native-json.md': 'docs/storage/native-json',
    'docs/HARDWARE.md': 'docs/hardware',
    'docs/GOALS.md': 'docs/goals',
    'docs/OPERATING.md': 'docs/operating',
}

# Read on demand (bytes). SAFETY and GUIDE are over LEAF and stay whole on purpose: SAFETY is binding and must be read
# in full before device work; GUIDE is for OP-XY owners and mod writers, who read it end to end.
CAPS = {
    'docs/SAFETY.md': 10200,  # holds host access too (first-install links to it)
    'docs/GUIDE.md': 11800,  # for OP-XY owners and mod writers, not agents (#65: Ask before USB control)
    'docs/MODS-DEV.md': 5500,
}

# Files outside docs/ whose relative links must resolve.
LINKED = ['AGENTS.md', 'ONBOARDING.md', 'README.md', 'web/README.md', 'clients/ts/README.md']

# The packages' READMEs: the leaf budget, as any other doc.
PACKAGE_READMES = ['web/README.md', 'clients/ts/README.md']

LINK = re.compile(r'\]\(([^)\s]+)(?:\s+"[^"]*")?\)')
HEADING = re.compile(r'^#{1,6}\s+(.*?)\s*#*\s*$')


def size(path):
    return len(path.read_bytes())


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
    return sorted(DOCS.rglob('*.md'))


def rel(path):
    return str(path.relative_to(ROOT))


class DocsBudget(unittest.TestCase):
    def check(self, path, budget):
        with self.subTest(doc=rel(path)):
            self.assertLessEqual(size(path), budget, f'{rel(path)} is over its budget: split it into a tree')

    def test_first_reads(self):
        for name, budget in FIRST_READS.items():
            self.check(ROOT / name, budget)

    def test_caps(self):
        for name, cap in CAPS.items():
            self.check(ROOT / name, cap)

    def test_trees(self):
        for name, folder in TREES.items():
            index, folder = ROOT / name, ROOT / folder
            self.assertTrue(folder.is_dir(), f'{name}: its leaves belong in {rel(folder)}')
            self.check(index, INDEX)
            leaves = sorted(folder.glob('*.md'))
            for leaf in leaves:
                self.check(leaf, LEAF)
            targets = [(index.parent / f).resolve() for f, _ in links(index) if f]
            with self.subTest(index=name):
                for leaf in leaves:
                    self.assertEqual(targets.count(leaf.resolve()), 1, f'{name} must link {rel(leaf)} exactly once')

    def test_links(self):
        for path in docs() + [ROOT / n for n in LINKED]:
            broken = []
            for file, anchor in links(path):
                target = Path(os.path.normpath(path.parent / file)) if file else path
                if not target.exists():
                    broken.append(f'{file}#{anchor}' if anchor else file)
                elif anchor and target.suffix == '.md' and anchor not in slugs(target):
                    broken.append(f'{file}#{anchor}')
            with self.subTest(doc=rel(path)):
                self.assertEqual(broken, [], f'{rel(path)} has broken links')

    def test_package_readmes(self):
        for name in PACKAGE_READMES:
            self.check(ROOT / name, LEAF)

    def test_every_other_doc_is_a_leaf(self):
        """A doc that is neither budgeted above nor in a tree gets the leaf budget: a new doc needs no list entry."""
        listed = {ROOT / n for n in [*FIRST_READS, *CAPS, *TREES]} | {ROOT / f for f in TREES.values()}
        for path in docs():
            if path not in listed and path.parent not in listed:
                self.check(path, LEAF)


if __name__ == '__main__':
    unittest.main()
