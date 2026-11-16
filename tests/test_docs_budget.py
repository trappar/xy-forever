"""Doc budgets and the allowed docs/ files (docs/ORCHESTRATION.md, "Where things go")."""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / 'docs'
DECISIONS = DOCS / 'decisions'

BUDGETS = {
    'docs/PLAN.md': 160,
    # The local docs/STATUS.md is gitignored and not budget-checked; the committed template is.
    'docs/STATUS.template.md': 40,
    'docs/ORCHESTRATION.md': 125,
    'AGENTS.md': 80,
    'ONBOARDING.md': 150,
    # Generated from protocol/v1.json (scripts/protogen.py): one section per published command,
    # with its request and reply tables, so it outgrows the 300-line reference budget. 430 lines
    # for 14 commands at 1.0; 458 at 1.1 (HELLO keys, three status codes); 482 with 1.2e (transport table,
    # decoding/optional/text rules, common statuses); 490 with 1.4 (conformance suite section); 586 with
    # 1.5 (file.list, file.delete, file.rmdir, file.mkdir); 605 with 1.7 (library.boot: 17 generated lines); 1.8
    # (file.rename, 22 lines) paid for by one-line examples and inline common statuses and reserved numbers;
    # 1.9 (file.copy) by each command's summary and description in one paragraph; 1.10 (mods.apply, job.status,
    # job.cancel: 59 lines) by the request and reply sizes in their tables' heads.
    'docs/PROTOCOL.md': 610,
    # For OP-XY owners and mod writers, not agents (plan 9.1).
    'docs/GUIDE.md': 220,
    'docs/MODS-DEV.md': 120,
}
DECISION_BUDGET = 60
# Reference docs (plan 0.4b): each stays within one budget (PROTOCOL.md has its own, above).
REFERENCE = ('ARCHITECTURE.md', 'GOALS.md', 'HARDWARE.md', 'HISTORY.md', 'OPERATING.md', 'SAFETY.md', 'STORAGE.md')
REFERENCE_BUDGET = 300

ALLOWED = {
    'ARCHITECTURE.md', 'GOALS.md', 'GUIDE.md', 'HARDWARE.md', 'HISTORY.md', 'MODS-DEV.md', 'OPERATING.md',
    'ORCHESTRATION.md', 'PLAN.md', 'PROTOCOL.md', 'SAFETY.md', 'STATUS.md', 'STATUS.template.md',
    'STORAGE.md',
}


def lines(path):
    return len(path.read_text(encoding='utf-8').splitlines())


class DocsBudget(unittest.TestCase):
    def test_budgets(self):
        for rel, budget in BUDGETS.items():
            with self.subTest(doc=rel):
                self.assertLessEqual(lines(ROOT / rel), budget, f'{rel} is over its budget')

    def test_reference_docs(self):
        for name in REFERENCE:
            path = DOCS / name
            with self.subTest(doc=name):
                self.assertLessEqual(lines(path), REFERENCE_BUDGET, f'docs/{name} is over its budget')

    def test_decision_records(self):
        for path in sorted(DECISIONS.glob('*.md')):
            with self.subTest(doc=path.name):
                self.assertLessEqual(lines(path), DECISION_BUDGET,
                                     f'docs/decisions/{path.name} is over its budget')

    def test_only_allowed_docs(self):
        extra = []
        for path in DOCS.rglob('*.md'):
            if path.parent == DECISIONS:
                continue
            if path.parent != DOCS or path.name not in ALLOWED:
                extra.append(str(path.relative_to(ROOT)))
        self.assertEqual(sorted(extra), [], 'docs/ holds files outside the allowed set')


if __name__ == '__main__':
    unittest.main()
