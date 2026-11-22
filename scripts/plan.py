#!/usr/bin/env python3
"""The work queue, from GitHub issues (the plan): one line per issue, so the orchestrator reads little.

    python3 scripts/plan.py            yours (in progress), next (unblocked, unassigned), decisions waiting
    python3 scripts/plan.py --all      also blocked items, items taken by others, and threads
    python3 scripts/plan.py --agent NAME   yours split by the `agent:<name>` label: NAME's, another agent's, unlabelled

Order: milestone (its GitHub number, the roadmap's order), then issue number. Every open issue is an item but
a `thread` (a loose end) or a `decision` (a question I have not decided yet); its other labels are shown with it
(`device`: needs the OP-XY; `device:risky`), and `blocked` stops it. Needs are GitHub "blocked by" dependencies.
The issue number is the item's only name.
Uses the `gh` CLI (authenticated); docs/ORCHESTRATION.md says how the queue is worked. An agent takes an item with
the assignee and an `agent:<name>` label (docs/operating/two-agents.md); an item with such a label is taken.
"""

import argparse
import json
import subprocess
import sys

AGENT = 'agent:'
NOT_SHOWN = {'blocked', 'thread', 'decision'}


def fetch():
    out = subprocess.run(
        ['gh', 'api', '--paginate', 'repos/{owner}/{repo}/issues?state=open&per_page=100'],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    issues = json.loads(out.replace('][', ','))  # --paginate concatenates the pages' arrays
    me = subprocess.run(
        ['gh', 'api', 'user', '--jq', '.login'], capture_output=True, text=True, check=True
    ).stdout.strip()
    return [i for i in issues if 'pull_request' not in i], me


def fields(issue):
    labels = [l['name'] for l in issue.get('labels', [])]
    milestone = issue.get('milestone') or {}
    return dict(
        number=issue['number'],
        title=issue['title'],
        kinds=sorted(l.removeprefix('kind:') for l in labels if not l.startswith(AGENT) and l not in NOT_SHOWN),
        labels=set(labels),
        milestone=milestone.get('number', 10**6),
        mkey=(milestone.get('title') or '-').split('.')[0],
        assignees=[a['login'] for a in issue.get('assignees') or []],
        agents=sorted(l[len(AGENT) :] for l in labels if l.startswith(AGENT)),
        blocked_by=(issue.get('issue_dependencies_summary') or {}).get('blocked_by', 0),
    )


def line(f, extra=''):
    kind = ','.join(f['kinds'])
    return '#%d %s %s%s%s' % (f['number'], f['mkey'], f['title'], ' [%s]' % kind if kind else '', extra)


def queue(issues, me, everything=False, agent=None):
    """The plan's lines. With agent: the items labelled agent:<agent>, those another agent's label holds, and
    the rest. Without: an item with an agent label counts as taken."""
    rows = sorted((fields(i) for i in issues), key=lambda f: (f['milestone'], f['number']))
    items = [f for f in rows if not f['labels'] & {'thread', 'decision'}]
    ours = [f for f in items if agent in f['agents']] if agent else []
    theirs = [f for f in items if f['agents'] and f not in ours] if agent else []
    items = [f for f in items if not f['agents']] if agent else items
    mine = [f for f in items if me in f['assignees']]
    others = [f for f in items if f not in mine and (f['assignees'] or f['agents'])]
    free = [f for f in items if not f['assignees'] and not f['agents']]
    ready = [f for f in free if not f['blocked_by'] and 'blocked' not in f['labels']]
    waiting = [f for f in free if f['blocked_by'] or 'blocked' in f['labels']]
    decisions = [f for f in rows if 'decision' in f['labels']]
    threads = [f for f in rows if 'thread' in f['labels']]
    out = []

    def section(title, fs, extra=lambda f: ''):
        out.append('%s (%d)' % (title, len(fs)))
        out.extend('  ' + line(f, extra(f)) for f in fs)

    if agent:
        section('Yours, in progress (%s%s)' % (AGENT, agent), ours)
        section('Yours, no agent label: interrupted, label it to resume', mine)
        section("Another agent's: leave these", theirs, lambda f: ' (%s)' % ','.join(AGENT + a for a in f['agents']))
    else:
        section('Yours, in progress', mine)
    section('Next: unblocked, unassigned', ready)
    section('Decisions waiting for me', decisions)
    if everything:
        section('Blocked', waiting, lambda f: ' (blocked by %d)' % f['blocked_by'] if f['blocked_by'] else ' (label)')
        section(
            'Taken by others',
            others,
            lambda f: ' ' + ' '.join(['@' + a for a in f['assignees']] + [AGENT + a for a in f['agents']]),
        )
        section('Threads (not yet items)', threads)
    else:
        out.append(
            'Not shown: %d blocked, %d taken by others, %d threads (--all)' % (len(waiting), len(others), len(threads))
        )
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--all', action='store_true', help='also blocked items, items taken by others, threads')
    ap.add_argument('--agent', help='your agent name: split your items by the agent:<name> label')
    args = ap.parse_args(argv)
    issues, me = fetch()
    print('\n'.join(queue(issues, me, args.all, args.agent)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
