"""Doc budgets and the allowed docs/ files (docs/ORCHESTRATION.md, "Where things go").

Budgets are bytes, not lines: a line budget is met by writing longer lines. What every agent reads first
also has a line-length limit. Over budget: split into a tree (an index plus leaves), do not squeeze.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / 'docs'
DECISIONS = DOCS / 'decisions'
STATUS = DOCS / 'status'            # local and gitignored: only its index is checked, when present

# Read by every agent, or by the orchestrator every sitting: (bytes, longest line).
FIRST_READS = {
    'AGENTS.md': (6500, 160),
    'ONBOARDING.md': (10000, 160),
    'docs/ORCHESTRATION.md': (9600, 190),
    'docs/STATUS.template.md': (2500, 120),
}
LOCAL_STATUS = 6000                 # docs/status/README.md; its leaves are not checked

# Read on demand, capped at about their size until plan item A.2 splits them into trees (bytes).
CAPS = {
    'docs/ARCHITECTURE.md': 25600,
    'docs/GOALS.md': 14200,
    'docs/HARDWARE.md': 20200,
    'docs/HISTORY.md': 6600,
    'docs/OPERATING.md': 13800,
    'docs/SAFETY.md': 11500,
    'docs/STORAGE.md': 25000,
    'docs/PROTOCOL.md': 56000,      # generated from protocol/v1.json (scripts/protogen.py)
    'docs/GUIDE.md': 11500,         # for OP-XY owners and mod writers, not agents (plan 9.1)
    'docs/MODS-DEV.md': 5500,
}
DECISION_CAP = 16000

ALLOWED = {
    'ARCHITECTURE.md', 'GOALS.md', 'GUIDE.md', 'HARDWARE.md', 'HISTORY.md', 'MODS-DEV.md', 'OPERATING.md',
    'ORCHESTRATION.md', 'PROTOCOL.md', 'SAFETY.md', 'STATUS.template.md', 'STORAGE.md',
}


def size(path):
    return len(path.read_bytes())


def longest(path):
    return max((len(l) for l in path.read_text(encoding='utf-8').splitlines()), default=0)


class DocsBudget(unittest.TestCase):
    def check(self, path, budget, width=None):
        rel = path.relative_to(ROOT)
        with self.subTest(doc=str(rel)):
            self.assertLessEqual(size(path), budget, f'{rel} is over its budget: split it into a tree')
            if width:
                self.assertLessEqual(longest(path), width, f'{rel} has a line over {width} characters')

    def test_first_reads(self):
        for rel, (budget, width) in FIRST_READS.items():
            self.check(ROOT / rel, budget, width)

    def test_local_status_index(self):
        index = STATUS / 'README.md'
        if not index.is_file():
            self.skipTest('no local docs/status/README.md')
        self.assertLessEqual(size(index), LOCAL_STATUS,
                             'your local docs/status/README.md is over 6 KB: move detail into leaf files beside it')

    def test_caps(self):
        for rel, cap in CAPS.items():
            self.check(ROOT / rel, cap)
        for path in sorted(DECISIONS.glob('*.md')):
            self.check(path, DECISION_CAP)

    def test_only_allowed_docs(self):
        extra = []
        for path in DOCS.rglob('*.md'):
            if path.parent == DECISIONS or STATUS in path.parents:
                continue
            if path.parent != DOCS or path.name not in ALLOWED:
                extra.append(str(path.relative_to(ROOT)))
        self.assertEqual(sorted(extra), [], 'docs/ holds files outside the allowed set')


if __name__ == '__main__':
    unittest.main()
