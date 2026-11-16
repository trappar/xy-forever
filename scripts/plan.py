#!/usr/bin/env python3
"""The work queue, from GitHub issues (the plan): one line per issue, so the orchestrator reads little.

    python3 scripts/plan.py            yours (in progress), next (unblocked, unassigned), decisions waiting
    python3 scripts/plan.py --all      also blocked items, items taken by others, and threads

Order: milestone (its GitHub number, the roadmap's order), then issue number. An item is an issue with a
`kind:*` label (its kind); `blocked` stops it, `thread` marks a loose end and `decision` a question for the
developer. Needs are GitHub "blocked by" dependencies. The issue number is the item's only name.
Uses the `gh` CLI (authenticated); docs/ORCHESTRATION.md says how the queue is worked.
"""
import argparse
import json
import subprocess
import sys


def fetch():
    out = subprocess.run(['gh', 'api', '--paginate', 'repos/{owner}/{repo}/issues?state=open&per_page=100'],
                         capture_output=True, text=True, check=True).stdout
    issues = json.loads(out.replace('][', ','))                 # --paginate concatenates the pages' arrays
    me = subprocess.run(['gh', 'api', 'user', '--jq', '.login'], capture_output=True, text=True,
                        check=True).stdout.strip()
    return [i for i in issues if 'pull_request' not in i], me


def fields(issue):
    labels = [l['name'] for l in issue.get('labels', [])]
    milestone = issue.get('milestone') or {}
    return dict(
        number=issue['number'], title=issue['title'],
        kinds=[l[5:] for l in labels if l.startswith('kind:')], labels=set(labels),
        milestone=milestone.get('number', 10 ** 6), mkey=(milestone.get('title') or '-').split('.')[0],
        assignees=[a['login'] for a in issue.get('assignees') or []],
        blocked_by=(issue.get('issue_dependencies_summary') or {}).get('blocked_by', 0))


def line(f, extra=''):
    kind = ','.join(f['kinds'])
    return '#%d %s %s%s%s' % (f['number'], f['mkey'], f['title'], ' [%s]' % kind if kind else '', extra)


def queue(issues, me, everything=False):
    rows = sorted((fields(i) for i in issues), key=lambda f: (f['milestone'], f['number']))
    items = [f for f in rows if f['kinds'] and not f['labels'] & {'thread', 'decision'}]
    mine = [f for f in items if me in f['assignees']]
    others = [f for f in items if f['assignees'] and me not in f['assignees']]
    free = [f for f in items if not f['assignees']]
    ready = [f for f in free if not f['blocked_by'] and 'blocked' not in f['labels']]
    waiting = [f for f in free if f['blocked_by'] or 'blocked' in f['labels']]
    decisions = [f for f in rows if 'decision' in f['labels']]
    threads = [f for f in rows if 'thread' in f['labels']]
    out = []

    def section(title, fs, extra=lambda f: ''):
        out.append('%s (%d)' % (title, len(fs)))
        out.extend('  ' + line(f, extra(f)) for f in fs)

    section('Yours, in progress', mine)
    section('Next: unblocked, unassigned', ready)
    section('Decisions waiting for the developer', decisions)
    if everything:
        section('Blocked', waiting, lambda f: ' (blocked by %d)' % f['blocked_by'] if f['blocked_by'] else ' (label)')
        section('Taken by others', others, lambda f: ' @' + ','.join(f['assignees']))
        section('Threads (not yet items)', threads)
    else:
        out.append('Not shown: %d blocked, %d taken by others, %d threads (--all)'
                   % (len(waiting), len(others), len(threads)))
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--all', action='store_true', help='also blocked items, items taken by others, threads')
    args = ap.parse_args(argv)
    issues, me = fetch()
    print('\n'.join(queue(issues, me, args.all)))
    return 0


if __name__ == '__main__':
    sys.exit(main())
