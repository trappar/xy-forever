"""Doc budgets, links and the allowed docs/ files (docs/ORCHESTRATION.md, "Where things go").

Budgets are bytes, not lines: a line budget is met by writing longer lines.
Over budget: split into a tree (an index plus leaves), do not squeeze.
"""
import os
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DOCS = ROOT / 'docs'
STATUS = DOCS / 'status'            # local and gitignored: not checked

LEAF = 8000                         # a current-reference leaf (bytes)
INDEX = 4000                        # the index of a tree (bytes)

# Read by every agent, or by the orchestrator every sitting (bytes).
FIRST_READS = {
    'AGENTS.md': 6500,
    'ONBOARDING.md': 10000,
    'docs/ORCHESTRATION.md': 9600,
    'docs/STATUS.template.md': 2500,
}

# A big doc keeps its path as the index of a tree; its leaves sit in the folder. Until the folder exists,
# the doc's cap in CAPS applies.
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
    'docs/ARCHITECTURE.md': 25600,
    'docs/HARDWARE.md': 20200,
    'docs/SAFETY.md': 11500,
    'docs/STORAGE.md': 25000,
    'docs/PROTOCOL.md': 56000,      # generated from protocol/v1.json (scripts/protogen.py)
    'docs/GUIDE.md': 11800,         # for OP-XY owners and mod writers, not agents (#65: Ask before USB control)
    'docs/MODS-DEV.md': 5500,
}

ALLOWED = {
    'ARCHITECTURE.md', 'GOALS.md', 'GUIDE.md', 'HARDWARE.md', 'MODS-DEV.md', 'OPERATING.md',
    'ORCHESTRATION.md', 'PROTOCOL.md', 'SAFETY.md', 'STATUS.template.md', 'STORAGE.md',
}

# Files whose relative links must resolve (docs/status/ is local and not checked).
LINKED = ['AGENTS.md', 'ONBOARDING.md', 'README.md']
BASE = {'docs/STATUS.template.md': STATUS}   # copied to docs/status/README.md: its links resolve from there

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
    return sorted(p for p in DOCS.rglob('*.md') if STATUS not in p.parents)


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

    def test_links(self):
        for path in docs() + [ROOT / n for n in LINKED]:
            broken = []
            for file, anchor in links(path):
                target = Path(os.path.normpath(BASE.get(rel(path), path.parent) / file)) if file else path   # lexical: docs/status/ may be absent
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
            if path.parent in folders or STATUS in path.parents:
                continue
            if path.parent != DOCS or path.name not in ALLOWED:
                extra.append(rel(path))
        self.assertEqual(sorted(extra), [], 'docs/ holds files outside the allowed set')


if __name__ == '__main__':
    unittest.main()
