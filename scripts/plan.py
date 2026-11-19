#!/usr/bin/env python3
"""The work queue, from GitHub issues (the plan): one line per issue, so the orchestrator reads little.

    python3 scripts/plan.py            yours (in progress), next (unblocked, unassigned), decisions waiting
    python3 scripts/plan.py --all      also blocked items, items taken by others, and threads
    python3 scripts/plan.py --agent NAME            yours split by local claim: NAME's, another agent's, unclaimed
    python3 scripts/plan.py claim N --agent NAME    claim item N on this machine; release N drops the claim

Order: milestone (its GitHub number, the roadmap's order), then issue number. An item is an issue with a
`kind:*` label (its kind; `device:safe` or `device:risky` is shown with it); `blocked` stops it, `thread`
marks a loose end and `decision` a question I have not decided yet. Needs are GitHub "blocked by" dependencies. The issue number is the item's only name.
Uses the `gh` CLI (authenticated); docs/ORCHESTRATION.md says how the queue is worked. Claims are local files
(xy_forever/claims.py; docs/operating/two-agents.md) and never reach GitHub. Without --agent, nothing reads them.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xy_forever import claims as C  # noqa: E402


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
        kinds=[l[5:] for l in labels if l.startswith('kind:')] + [l for l in labels if l.startswith('device:')],
        labels=set(labels),
        milestone=milestone.get('number', 10 ** 6), mkey=(milestone.get('title') or '-').split('.')[0],
        assignees=[a['login'] for a in issue.get('assignees') or []],
        blocked_by=(issue.get('issue_dependencies_summary') or {}).get('blocked_by', 0))


def line(f, extra=''):
    kind = ','.join(f['kinds'])
    return '#%d %s %s%s%s' % (f['number'], f['mkey'], f['title'], ' [%s]' % kind if kind else '', extra)


def queue(issues, me, everything=False, agent=None, held=None, stale=lambda claim: False):
    """The plan's lines. With agent: the items held (local claims: {number: claim}) by agent, by another agent
    and the rest; stale(claim) marks a claim whose branch has gone quiet."""
    rows = sorted((fields(i) for i in issues), key=lambda f: (f['milestone'], f['number']))
    items = [f for f in rows if f['kinds'] and not f['labels'] & {'thread', 'decision'}]
    held = held if agent else {}
    ours = [f for f in items if f['number'] in held and held[f['number']].get('agent') == agent]
    theirs = [f for f in items if f['number'] in held and f not in ours]
    items = [f for f in items if f['number'] not in held]
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

    def claimed(f):
        c = held[f['number']]
        return ' (%s%s)' % (c['agent'] if c['agent'] != agent else c.get('branch') or '-',
                            ', stale: no commits for 2 days' if stale(c) else '')

    if agent:
        section('Yours, in progress (claimed by %s)' % agent, ours, claimed)
        section('Yours, unclaimed: interrupted, claimable', mine)
        section('Claimed by another agent: leave these', theirs, claimed)
    else:
        section('Yours, in progress', mine)
    section('Next: unblocked, unassigned', ready)
    section('Decisions waiting for me', decisions)
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
    ap.add_argument('--agent', help='your agent name (%s): split your items by local claim' % ', '.join(C.AGENTS))
    sub = ap.add_subparsers(dest='command')
    c = sub.add_parser('claim', help='claim item N for --agent NAME on this machine (a local file, never on GitHub)')
    c.add_argument('number', type=int)
    c.add_argument('--agent', required=True)
    r = sub.add_parser('release', help="drop the local claim on item N (with --agent: only that agent's)")
    r.add_argument('number', type=int)
    r.add_argument('--agent')
    args = ap.parse_args(argv)
    try:
        if args.command == 'claim':
            got = C.claim(args.number, C.check_agent(args.agent))
            print('#%d claimed by %s (branch %s, %s)' % (args.number, got['agent'], got['branch'] or '-',
                                                        got['worktree']))
            return 0
        if args.command == 'release':
            got = C.release(args.number, args.agent)
            print('#%d released (%s)' % (args.number, got['agent']) if got else '#%d was not claimed' % args.number)
            return 0
        if args.agent:
            C.check_agent(args.agent)
            held = C.load()
            gone = C.sweep(held)
        issues, me = fetch()
    except C.ClaimError as error:
        print('refused: %s' % error, file=sys.stderr)
        return 1
    if not args.agent:
        print('\n'.join(queue(issues, me, args.all)))
        return 0
    print('\n'.join(queue(issues, me, args.all, args.agent, held, C.stale)))
    for number, claim in gone:
        print('Released #%d (%s): its pull request is merged or closed' % (number, claim['agent']))
    return 0


if __name__ == '__main__':
    sys.exit(main())
