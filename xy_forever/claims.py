"""Local claims: which agent on this machine works which plan item (scripts/plan.py claim, release, --agent).

A claim is <private>/xy-forever/claims/<issue>.json, made with an exclusive create, so of two agents claiming one
item at once exactly one wins and the other is told who holds it. It records the agent's name (one of AGENTS, the
fixed list in docs/operating/two-agents.md), the branch, the worktree and the time, and carries VERSION; a reader
refuses a newer version with a message. Claims never leave this machine: nothing here writes to GitHub (only
pr_states reads it).
"""
import datetime
import json
import os
from pathlib import Path
import subprocess

from xy_forever import paths

VERSION = 1
AGENTS = ('claude', 'codex')     # the agents' names: each tool's own (docs/operating/two-agents.md)
STALE = 2 * 24 * 3600            # seconds without a commit on the claim's branch before it shows as stale


class ClaimError(Exception):
    """A refusal, its message for the user (scripts/plan.py prints it and exits 1)."""


def check_agent(name):
    if name not in AGENTS:
        raise ClaimError('--agent %s: not one of the agent names (%s; docs/operating/two-agents.md)'
                         % (name, ', '.join(AGENTS)))
    return name


def git(root, *args):
    """git's output in root, or '' when it fails."""
    run = subprocess.run(['git', *args], cwd=str(root), capture_output=True, text=True)
    return run.stdout.strip() if run.returncode == 0 else ''


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')


def epoch(stamp):
    return datetime.datetime.strptime(stamp, '%Y-%m-%dT%H:%M:%SZ').replace(tzinfo=datetime.timezone.utc).timestamp()


def read(path):
    """The claim at path (a dict); ClaimError when it is unreadable or newer than this reader."""
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, ValueError) as error:
        raise ClaimError('claim %s is unreadable (%s): remove it or `plan.py release`' % (path, error))
    if not isinstance(data, dict) or not isinstance(data.get('version'), int):
        raise ClaimError('claim %s has no version: remove it or `plan.py release`' % path)
    if data['version'] > VERSION:
        raise ClaimError('claim %s is version %d; this plan.py reads %d: update this checkout'
                         % (path, data['version'], VERSION))
    return data


def load(folder=None):
    """{issue number: claim} of every claim; {} when there is no claims folder."""
    folder = Path(folder or paths.CLAIMS)
    if not folder.is_dir():
        return {}
    return {int(p.stem): read(p) for p in sorted(folder.glob('*.json')) if p.stem.isdigit()}


def claim(number, agent, root=paths.REPO, folder=None, stamp=None):
    """Claims issue number for agent, recording root's branch and worktree; ClaimError naming the holder when the
    item is already claimed. Returns the claim."""
    check_agent(agent)
    folder = Path(folder or paths.CLAIMS)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ('%d.json' % number)
    data = dict(version=VERSION, issue=number, agent=agent, branch=git(root, 'branch', '--show-current'),
                worktree=git(root, 'rev-parse', '--show-toplevel') or str(root), time=stamp or now())
    try:
        fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
    except FileExistsError:
        held = read(path)
        if held.get('agent') == agent and held.get('worktree') == data['worktree']:
            return held
        raise ClaimError('#%d is claimed by %s (branch %s, %s, since %s): leave it to that agent'
                         % (number, held.get('agent'), held.get('branch') or '-', held.get('worktree'), held.get('time')))
    with os.fdopen(fd, 'w') as f:
        json.dump(data, f, indent=2)
        f.write('\n')
    return data


def release(number, agent=None, folder=None):
    """Removes the claim on issue number (with agent: only that agent's); returns it, or None when there was none."""
    path = Path(folder or paths.CLAIMS) / ('%d.json' % number)
    if not path.exists():
        return None
    held = read(path)
    if agent and held.get('agent') != agent:
        raise ClaimError('#%d is claimed by %s, not %s: only that agent releases it' % (number, held.get('agent'), agent))
    path.unlink(missing_ok=True)
    return held


def pr_states(branch, root=paths.REPO):
    """The states (OPEN, MERGED, CLOSED) of the pull requests from branch, via the gh CLI; None when gh fails."""
    run = subprocess.run(['gh', 'pr', 'list', '--head', branch, '--state', 'all', '--json', 'state'],
                         cwd=str(root), capture_output=True, text=True)
    if run.returncode:
        return None
    return {p['state'] for p in json.loads(run.stdout or '[]')}


def last_commit(branch, root=paths.REPO):
    """When branch last got a commit of its own (not on main or origin/main), or None."""
    bases = [b for b in ('origin/main', 'main') if git(root, 'rev-parse', '--verify', '-q', b)]
    if not branch or not git(root, 'rev-parse', '--verify', '-q', branch):
        return None
    stamp = git(root, 'log', '-1', '--format=%ct', branch, '--not', *bases)
    return float(stamp) if stamp else None


def sweep(claims, states=None, folder=None, open_issues=None):
    """Removes the claims whose issue is closed (not in open_issues, the open issue numbers, when given) or whose
    branch's pull requests (states(branch): pr_states) are all merged or closed: [(number, claim, why)] removed."""
    states, gone = states or pr_states, []
    for number, c in sorted(claims.items()):
        if open_issues is not None and number not in open_issues:
            why = 'its issue is closed'
        else:
            found = states(c['branch']) if c.get('branch') else None
            if not found or 'OPEN' in found:
                continue
            why = 'its pull request is merged or closed'
        release(number, folder=folder)
        gone.append((number, claims.pop(number), why))
    return gone


def stale(c, last=None, at=None):
    """True when the claim's branch has had no commit of its own (last(branch): last_commit), nor the claim been
    made, for STALE seconds."""
    last = last or last_commit
    since = max(filter(None, (last(c.get('branch')), epoch(c['time']) if c.get('time') else None)), default=None)
    return since is not None and (at or datetime.datetime.now().timestamp()) - since > STALE
