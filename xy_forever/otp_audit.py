"""Instruction and data rules for the product's narrowly scoped OTP reader."""
import re

from .build_formats import require

BOOT_ROM = (0x04000000, 0x04100000)
OTP_CALLS = {'04000048', '04000054'}
OTP_READ, OTP_INIT, OTP_INIT_FLAG = 0x04000048, 0x04000054, 0x11b01650


def function_bodies(listing):
    """{function label: [its lines]} of an objdump listing."""
    bodies, current = {}, None
    for line in listing.splitlines():
        m = re.match(r'^[0-9a-f]{8} <(.+)>:$', line)
        if m:
            current = m.group(1)
            bodies[current] = []
        elif current is not None:
            bodies[current].append(line)
    return bodies


def referencing_symbols(listing, part):
    """Functions that call, jump to or load the address of a function whose label contains part,
    other than such functions themselves (a branch inside one is not a reference)."""
    users = set()
    for name, lines in function_bodies(listing).items():
        if part in name:
            continue
        if any(re.search(r'<[^>]*%s[^>]*>' % re.escape(part), line) for line in lines):
            users.add(name)
    return users


HALF = re.compile(r'\b([pr][0-7])\.([hl]) = (?:0x)?([0-9a-f]+);')
ASSIGN = re.compile(r'\b([pr][0-7])(?:\.[hl])? =')


def loaded_values(lines):
    """32-bit constants a function body builds in a register from its two 16-bit halves (in either
    order, in straight-line code, nothing else written to the register between). objdump's own =0x comment
    cannot be used: on the first half it shows the register's stale other half, and it drops
    leading zeros (P2=0x4000048)."""
    values, halves = set(), {}
    for line in lines:
        code = (line.split('\t')[2] if line.count('\t') >= 2 else '').split('/*')[0].lower()
        if re.search(r'\b(?:jump|call|rts|rti)\b', code):
            halves = {}                                # halves pair up only in straight-line code
            continue
        half = HALF.search(code)
        if half:
            reg, which, value = half.group(1), half.group(2), int(half.group(3), 16)
            known = halves.setdefault(reg, {})
            known[which] = value & 0xffff
            if len(known) == 2:
                values.add(known['h'] << 16 | known['l'])
            continue
        for reg in ASSIGN.findall(code):
            halves.pop(reg, None)
    return values


def functions_loading(listing, address):
    """Function labels whose bodies build the constant address (loaded_values)."""
    return {name for name, lines in function_bodies(listing).items() if address in loaded_values(lines)}


def rom_references(listing):
    """Boot-ROM addresses the module builds in a register, as 8-digit hex."""
    found = set()
    for lines in function_bodies(listing).values():
        found |= loaded_values(lines)
    return {'%08x' % a for a in found if BOOT_ROM[0] <= a < BOOT_ROM[1]}


def check_otp_rules(raw, symbols, enabled):
    """tfw.check's OTP provider: the ROM read and init are each loaded by one stock<> wrapper of
    target/otp_keys.cpp, those wrappers and the init flag are used only inside otp_keys, and the
    provider's functions are reached only from the tfw.check handler. Without the feature, none of it."""
    has = lambda text: any(text in name for name in symbols)                  # noqa: E731
    if not enabled:
        require(not rom_references(raw) & OTP_CALLS and not has('otp_keys') and not has('tfw_check'),
                'OTP provider or tfw.check linked without XYF_TFW_CHECK')
        return
    for address in (OTP_READ, OTP_INIT):
        users = functions_loading(raw, address)
        require(len(users) == 1, 'ROM OTP entry %08x loaded by %d functions' % (address, len(users)))
        wrapper = next(iter(users))
        require('8otp_keys' in wrapper and 'stockILj%dE' % address in wrapper,
                'ROM OTP entry %08x loaded outside otp_keys stock<>: %s' % (address, wrapper))
        outside = {u for u in referencing_symbols(raw, wrapper) if '8otp_keys' not in u}
        require(not outside, 'ROM OTP wrapper reached outside otp_keys: ' + ', '.join(sorted(outside)))
    flag = functions_loading(raw, OTP_INIT_FLAG)
    require(flag and all('8otp_keys' in n for n in flag), 'OTP init flag used outside otp_keys: ' + ', '.join(sorted(flag)))
    callers = referencing_symbols(raw, '8otp_keys')
    require(callers and all('tfw_check' in c for c in callers),
            'OTP provider reached outside the tfw.check handler: ' + ', '.join(sorted(callers)))

