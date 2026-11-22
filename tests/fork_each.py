"""Many runs of a sanitized host binary for one sanitizer start (tests/fork_each.cpp).

A test that runs a sanitized binary on many inputs adds FLAGS to its compile, then calls run(): each run is a
fork of one started process, so the sanitizer runtime's start (about 0.2 s of CPU a process on CI's macOS VMs,
against 0.01 s on an M-series Mac) is paid once, not per input.
"""

import subprocess
from pathlib import Path

from xy_forever import bounds

SOURCE = Path(__file__).resolve().parent / 'fork_each.cpp'
FLAGS = ('-Dmain=fork_each_main', str(SOURCE))


def run(binary, runs, work):
    """runs: [(args, stdin bytes)], each arg free of tabs and newlines; work: a folder for the runs' files.
    Returns a CompletedProcess per run, in order, with its exit status and its stdout and stderr bytes."""
    work = Path(work)
    lines = []
    for n, (args, stdin) in enumerate(runs):
        files = [work / ('each-%d.%s' % (n, kind)) for kind in ('in', 'out', 'err')]
        files[0].write_bytes(stdin)
        lines.append('\t'.join(map(str, [*files, *args])) + '\n')
    (work / 'each.runs').write_text(''.join(lines))
    done = subprocess.run(
        [str(binary), str(work / 'each.runs')], capture_output=True, check=True, timeout=bounds.TEST_S
    )
    codes = [int(line) for line in done.stdout.split()]
    if len(codes) != len(runs):
        raise AssertionError('fork_each: %d of %d runs reported\n%s' % (len(codes), len(runs), done.stderr))
    return [
        subprocess.CompletedProcess(
            [str(binary), *map(str, args)],
            code,
            (work / ('each-%d.out' % n)).read_bytes(),
            (work / ('each-%d.err' % n)).read_bytes(),
        )
        for n, ((args, _), code) in enumerate(zip(runs, codes))
    ]
