"""Call-site checks for the content switch's managed links (#202): the direct writer and the hardlink creator."""

import re

from .build_formats import require
from .otp_audit import functions_loading, referencing_symbols

HARDLINK_CREATOR = '88a0199a'  # stock yaffs object creator (type 4: a hardlink)


def check_state_write(raw, address, managed):
    """yaffs_write is loaded only by the y_write wrapper, reached only from the state write and, with the content
    switch, the sealed blob copy and the sealed file B writes itself (#195)."""
    users = set()
    current = None
    for line in raw.splitlines():
        label = re.match(r'^[0-9a-f]{8} <(.+)>:$', line)
        if label:
            current = label.group(1)
        elif '=0x%08x' % address in line and (
            '.l = 0x%x' % (address & 0xFFFF) in line.lower() or re.search(r'\b[pr]\d = 0x%x\b' % address, line.lower())
        ):
            # Only the instruction that completes the address (objdump annotates a .H load with a stale value).
            users.add(current)
    require(
        len(users) == 1 and 'y_write' in next(iter(users)),
        'yaffs_write loaded outside the y_write wrapper: ' + ', '.join(sorted(map(str, users))),
    )
    callers = referencing_symbols(raw, next(iter(users)))
    expected = ('write_state_slot', 'managed_blob_copy', 'managed_blob_write') if managed else ('write_state_slot',)
    require(
        len(callers) == len(expected) and all(any(name in caller for caller in callers) for name in expected),
        'y_write reached outside its scope: ' + ', '.join(sorted(map(str, callers))),
    )


def check_hardlink_scope(raw, has, managed):
    """The lookup, the creator and the sealed copy are linked only into builds with the content switch."""
    for symbol in (
        'hardlink_lookup',
        'hardlink_create_alias',
        'stage_blob_link',
        'managed_blob_open',
        'managed_blob_copy',
        'managed_blob_create',
        'managed_blob_write',
    ):
        require(has(symbol) == managed, 'Managed link symbol outside its build modes: ' + symbol)
    require(
        bool(functions_loading(raw, int(HARDLINK_CREATOR, 16))) == managed,
        'Hardlink creator outside the content switch builds',
    )
