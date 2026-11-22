"""scripts/plan.py: the work queue from GitHub issues (order, sections), on fixture issues; no network."""
import importlib.util
import unittest
from pathlib import Path

SPEC = importlib.util.spec_from_file_location('plan_script', Path(__file__).resolve().parents[1] / 'scripts/plan.py')
Q = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(Q)


def issue(number, title, milestone=None, labels=(), assignees=(), blocked_by=0):
    return {'number': number, 'title': title, 'body': '## Acceptance criteria\n- [ ] x',
            'labels': [{'name': l} for l in labels], 'assignees': [{'login': a} for a in assignees],
            'milestone': {'number': milestone[0], 'title': milestone[1]} if milestone else None,
            'issue_dependencies_summary': {'blocked_by': blocked_by}}


M2, M4 = (4, 'M2. File engine'), (6, 'M4. Packages')
ISSUES = [
    issue(11, 'Rename commit', M4, ['kind:build'], blocked_by=1),
    issue(13, 'Fixes', M4, ['kind:build']),
    issue(4, 'Atomic replace', M2, ['kind:build']),
    issue(3, 'Loose ends', M2, ['kind:build', 'kind:device'], ['me']),
    issue(2, 'Robustness', M2, ['kind:build'], ['someone']),
    issue(9, 'Probe', M2, ['kind:build', 'blocked']),
    issue(56, 'Default of the confirm gate', M4, ['decision']),
    issue(44, 'Suite race', None, ['thread']),
]


class Queue(unittest.TestCase):
    def test_device_risk_shown_with_the_kind(self):
        f = Q.fields(issue(8, 'Writer', M2, ['kind:build', 'device:risky', 'kind:device']))
        self.assertEqual(Q.line(f), '#8 M2 Writer [build,device,device:risky]')

    def test_sections_and_order(self):
        out = Q.queue(ISSUES, 'me')
        self.assertEqual(out, [
            'Yours, in progress (1)',
            '  #3 M2 Loose ends [build,device]',
            'Next: unblocked, unassigned (2)',
            '  #4 M2 Atomic replace [build]',          # milestone order first, then issue number
            '  #13 M4 Fixes [build]',
            'Decisions waiting for me (1)',
            '  #56 M4 Default of the confirm gate',
            'Not shown: 2 blocked, 1 taken by others, 1 threads (--all)'])

    def test_all_lists_blocked_others_and_threads(self):
        out = Q.queue(ISSUES, 'me', everything=True)
        self.assertIn('  #9 M2 Probe [build] (label)', out)
        self.assertIn('  #11 M4 Rename commit [build] (blocked by 1)', out)
        self.assertIn('  #2 M2 Robustness [build] @someone', out)
        self.assertIn('  #44 - Suite race', out)
        self.assertFalse(any(l.startswith('Not shown') for l in out))

    def test_with_agent_the_items_split_by_the_agent_label(self):
        issues = ISSUES + [issue(21, 'Mine labelled', M2, ['kind:build', 'agent:claude'], ['me']),
                           issue(22, 'Theirs', M2, ['kind:build', 'agent:codex'], ['me']),
                           issue(23, 'Free, theirs', M2, ['kind:build', 'agent:codex'])]
        out = Q.queue(issues, 'me', agent='claude')
        self.assertEqual(out[:7], [
            'Yours, in progress (agent:claude) (1)',
            '  #21 M2 Mine labelled [build]',
            'Yours, no agent label: interrupted, label it to resume (1)',
            '  #3 M2 Loose ends [build,device]',
            "Another agent's: leave these (2)",
            '  #22 M2 Theirs [build] (agent:codex)',
            '  #23 M2 Free, theirs [build] (agent:codex)'])
        self.assertIn('  #4 M2 Atomic replace [build]', out)

    def test_without_agent_a_labelled_item_is_taken(self):
        issues = ISSUES + [issue(23, 'Free, theirs', M2, ['kind:build', 'agent:codex'])]
        self.assertEqual(Q.queue(issues, 'me')[:4], Q.queue(ISSUES, 'me')[:4])
        self.assertIn('  #23 M2 Free, theirs [build] agent:codex', Q.queue(issues, 'me', everything=True))


if __name__ == '__main__':
    unittest.main()
