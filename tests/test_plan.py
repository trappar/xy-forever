"""scripts/plan.py: the work queue from GitHub issues (order, sections), on fixture issues; no network."""

import importlib.util
import json
import unittest
from pathlib import Path
from unittest import mock

SPEC = importlib.util.spec_from_file_location('plan_script', Path(__file__).resolve().parents[1] / 'scripts/plan.py')
Q = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(Q)


def issue(number, title, milestone=None, labels=(), assignees=(), blocked_by=0):
    return {
        'number': number,
        'title': title,
        'body': '## Acceptance criteria\n- [ ] x',
        'labels': [{'name': l} for l in labels],
        'assignees': [{'login': a} for a in assignees],
        'milestone': {'number': milestone[0], 'title': milestone[1]} if milestone else None,
        'issue_dependencies_summary': {'blocked_by': blocked_by},
    }


M2, M4 = (4, 'M2. File engine'), (6, 'M4. Packages')
ISSUES = [
    issue(11, 'Rename commit', M4, [], blocked_by=1),
    issue(13, 'Fixes', M4, []),
    issue(4, 'Atomic replace', M2, []),
    issue(3, 'Loose ends', M2, ['device'], ['me']),
    issue(2, 'Robustness', M2, [], ['someone']),
    issue(9, 'Probe', M2, ['blocked']),
    issue(56, 'Default of the confirm gate', M4, ['decision']),
    issue(44, 'Suite race', None, ['thread']),
]


class Queue(unittest.TestCase):
    def test_labels_shown_with_the_item(self):
        f = Q.fields(issue(8, 'Writer', M2, ['device:risky', 'device', 'blocked']))
        self.assertEqual(Q.line(f), '#8 M2 Writer [device,device:risky]')

    def test_pages_are_joined_and_pull_requests_dropped(self):
        pages = json.dumps([[issue(1, 'A')], [issue(2, 'B'), dict(issue(3, 'PR'), pull_request={})]])
        runs = [mock.Mock(stdout=pages), mock.Mock(stdout='me\n')]
        with mock.patch.object(Q.subprocess, 'run', side_effect=runs) as run:
            issues, me = Q.fetch()
        self.assertIn('--slurp', run.call_args_list[0].args[0])
        self.assertEqual(([i['number'] for i in issues], me), ([1, 2], 'me'))

    def test_sections_and_order(self):
        out = Q.queue(ISSUES, 'me')
        self.assertEqual(
            out,
            [
                'Yours, in progress (1)',
                '  #3 M2 Loose ends [device]',
                'Next: unblocked, unassigned (2)',
                '  #4 M2 Atomic replace',  # milestone order first, then issue number
                '  #13 M4 Fixes',
                'Decisions waiting for me (1)',
                '  #56 M4 Default of the confirm gate',
                'Not shown: 2 blocked, 1 taken by others, 1 threads (--all)',
            ],
        )

    def test_all_lists_blocked_others_and_threads(self):
        out = Q.queue(ISSUES, 'me', everything=True)
        self.assertIn('  #9 M2 Probe (label)', out)
        self.assertIn('  #11 M4 Rename commit (blocked by 1)', out)
        self.assertIn('  #2 M2 Robustness @someone', out)
        self.assertIn('  #44 - Suite race', out)
        self.assertFalse(any(l.startswith('Not shown') for l in out))


if __name__ == '__main__':
    unittest.main()
