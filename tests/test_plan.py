"""scripts/plan.py: the work queue from GitHub issues (order, sections), on fixture issues; no network."""
import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('plan_script', Path(__file__).resolve().parents[1] / 'scripts/plan.py')
Q = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(Q)


def issue(number, title, item=None, milestone=None, labels=(), assignees=(), blocked_by=0):
    return {'number': number, 'title': title, 'body': ('**Plan item %s**\n\nDone when: x.' % item) if item else 'text',
            'labels': [{'name': l} for l in labels], 'assignees': [{'login': a} for a in assignees],
            'milestone': {'number': milestone[0], 'title': milestone[1]} if milestone else None,
            'issue_dependencies_summary': {'blocked_by': blocked_by}}


M2, M4 = (4, 'M2. File engine'), (6, 'M4. Packages')
ISSUES = [
    issue(11, 'Rename commit', '4.1b', M4, ['kind:build'], blocked_by=1),
    issue(13, 'Fixes', '4.3f', M4, ['kind:build']),
    issue(4, 'Atomic replace', '2.2d', M2, ['kind:build']),
    issue(3, 'Loose ends', '2.2f2', M2, ['kind:build', 'kind:device'], ['me']),
    issue(2, 'Robustness', '1.5', M2, ['kind:build'], ['someone']),
    issue(9, 'Probe', '3.2', M2, ['kind:build', 'blocked']),
    issue(56, 'Default of the confirm gate', None, M4, ['decision']),
    issue(44, 'Suite race', None, None, ['thread']),
]


class Queue(unittest.TestCase):
    def test_sections_and_order(self):
        out = Q.queue(ISSUES, 'me')
        self.assertEqual(out, [
            'Yours, in progress (1)',
            '  #3 M2 2.2f2 Loose ends [build,device]',
            'Next: unblocked, unassigned (2)',
            '  #4 M2 2.2d Atomic replace [build]',          # milestone order first, then issue number
            '  #13 M4 4.3f Fixes [build]',
            'Decisions waiting for the developer (1)',
            '  #56 M4 Default of the confirm gate',
            'Not shown: 2 blocked, 1 taken by others, 1 threads (--all)'])

    def test_all_lists_blocked_others_and_threads(self):
        out = Q.queue(ISSUES, 'me', everything=True)
        self.assertIn('  #9 M2 3.2 Probe [build] (label)', out)
        self.assertIn('  #11 M4 4.1b Rename commit [build] (blocked by 1)', out)
        self.assertIn('  #2 M2 1.5 Robustness [build] @someone', out)
        self.assertIn('  #44 - Suite race', out)
        self.assertFalse(any(l.startswith('Not shown') for l in out))


if __name__ == '__main__':
    unittest.main()
