"""The `hello` text's generated parts (split out of scripts/protogen.py, which imports them): its key sources,
the cmds bitmap and the C++ macros."""
import itertools

# hello key sources besides a fixed value or constant: release and mode from the build (C macros below);
# set at run time: image (compiled `unknown`), confirm (#65, compiled 1: the default) and stage2 (compiled 0, so
# the last key).
HELLO_SOURCES = ('version', 'release', 'image', 'mode', 'cmds', 'dev', 'confirm', 'stage2')
STAGE2_DIGITS = 10                                # a u32 in decimal
# Switches no build defines: the device sets the command's cmds bit at run time (stage2.info: while
# stage 2 is loaded). The generated #if blocks give the digit without them.
RUNTIME_SWITCHES = ('XYF_STAGE2_LOADED',)
BUILD_MACROS = {'release': 'XYF_VERSION', 'mode': 'XYF_BUILD_MODE'}
IMAGE_DIGITS, IMAGE_UNKNOWN = 8, 'unknown'
CMDS_DIGITS = 32                                  # request types 0x00..0x7f, four per hex digit


def answered(spec, switches):
    """The hello cmds bitmap of a build compiled with these build switches."""
    return sum(1 << int(c['type'], 16) for c in spec['commands'] if c['switch'] is None or c['switch'] in switches)


def hello_source(key):
    return 'fixed' if 'value' in key or 'constant' in key or key['source'] == 'version' else key['source']


def hello_fixed(spec, key):
    """A key's text when it is the same in every build, else None."""
    if 'value' in key:
        return key['value']
    if 'constant' in key:
        return str({k['name']: k['value'] for k in spec['constants']}[key['constant']])
    return spec['protocol']['version'] if key['source'] == 'version' else None


def render_cpp_hello(spec):
    """Macros for the hello text: string literals and #if blocks only (no table, no code)."""
    lines = ['// hello text (docs/protocol/cmd-session.md, hello). The build defines %s and %s (string literals).' % (
                 BUILD_MACROS['release'], BUILD_MACROS['mode']),
             '// XYF_USBP_CMDS: the cmds bitmap, most significant hex digit first; a digit that a build switch',
             '// changes is one #if block keyed by the switches of its commands (as home_pump\'s dispatch).',
             '// %s: never defined by a build; the device sets those bits at run time.' % ', '.join(RUNTIME_SWITCHES)]
    cmds, run = [], ''
    for digit in range(CMDS_DIGITS - 1, -1, -1):                   # request types 4*digit .. 4*digit+3
        here = [c for c in spec['commands'] if int(c['type'], 16) >> 2 == digit]
        switches = sorted({c['switch'] for c in here if c['switch']})
        if not switches:
            run += '%x' % (answered(spec, ()) >> 4 * digit & 15)
            continue
        if run:
            cmds.append('"%s"' % run)
            run = ''
        name = 'XYF_USBP_CMDS_%02X' % (4 * digit)
        combos = list(itertools.product((True, False), repeat=len(switches)))
        for i, combo in enumerate(combos):
            condition = ' && '.join(('defined(%s)' if on else '!defined(%s)') % s for s, on in zip(switches, combo))
            lines.append('#if ' + condition if i == 0 else '#else' if i == len(combos) - 1 else '#elif ' + condition)
            on = [s for s, b in zip(switches, combo) if b]
            lines.append('#define %s "%x"' % (name, answered(spec, on) >> 4 * digit & 15))
        lines.append('#endif')
        cmds.append(name)
    if run:
        cmds.append('"%s"' % run)
    lines.append('#define XYF_USBP_CMDS ' + ' '.join(cmds))
    pieces = [spec['hello']['prefix']]                # literal text (str) and macros (('macro', name))
    for key in spec['hello']['keys']:
        source = hello_source(key)
        if source == 'fixed':
            pieces.append(';%s=%s' % (key['name'], hello_fixed(spec, key)))
        elif source == 'dev':
            dev = [c for c in spec['commands'] if c['class'] == 'dev']
            item = '";%s=1"' % key['name']
            if any(c['switch'] is None for c in dev):
                lines.append('#define XYF_USBP_HELLO_DEV ' + item)
            elif dev:
                lines += ['#if ' + ' || '.join('defined(%s)' % s for s in sorted({c['switch'] for c in dev})),
                          '#define XYF_USBP_HELLO_DEV ' + item, '#else', '#define XYF_USBP_HELLO_DEV ""', '#endif']
            else:
                lines.append('#define XYF_USBP_HELLO_DEV ""')
            pieces.append(('macro', 'XYF_USBP_HELLO_DEV'))
        elif source == 'image':
            pieces.append(';%s=%s' % (key['name'], IMAGE_UNKNOWN))
        elif source in ('stage2', 'confirm'):        # usb_service.cpp: the loaded stage 2's version, the setting
            pieces.append(';%s=%d' % (key['name'], source == 'confirm'))
        else:
            pieces += [';%s=' % key['name'], ('macro', BUILD_MACROS.get(source, 'XYF_USBP_CMDS'))]
    merged = []
    for piece in pieces:
        if isinstance(piece, str) and merged and isinstance(merged[-1], str):
            merged[-1] += piece
        else:
            merged.append(piece)
    text = ['"%s"' % p if isinstance(p, str) else p[1] for p in merged]
    lines.append('#define XYF_USBP_HELLO_TEXT \\\n    ' + ' \\\n    '.join(text))
    return lines
