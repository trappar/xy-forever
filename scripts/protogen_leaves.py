"""Generated protocol documentation tree layout (split leaves before their budget grows) and its line folding."""
import re

DOC_DIR = 'docs/protocol'
CMD_LEAVES = (   # (first type, last type, file, title)
    (0x00, 0x0f, 'cmd-session.md', 'session commands'),
    (0x10, 0x13, 'cmd-stage.md', 'staging commands'),
    (0x14, 0x17, 'cmd-files.md', 'file commands'),
    (0x18, 0x1f, 'cmd-file-move.md', 'file move and copy commands'),
    (0x20, 0x3f, 'cmd-boot.md', 'boot and device commands'),
    (0x40, 0x43, 'cmd-library.md', 'library commands'),
    (0x44, 0x47, 'cmd-jobs.md', 'job commands'),
    (0x48, 0x48, 'cmd-firmware.md', 'firmware diagnostics'),
    (0x49, 0x4c, 'cmd-storage.md', 'package and storage reports'),
    (0x4d, 0x7f, 'cmd-config.md', 'saved inputs'),
)
DOC_LEAVES = tuple('%s/%s' % (DOC_DIR, f) for f in (
    ['transport.md', 'rules.md', 'statuses.md', 'commands.md'] + [leaf[2] for leaf in CMD_LEAVES] + ['examples.md', 'events-dev.md']))

WIDTH = 160                              # longest prose line in a generated page (tables and headings excepted)
LINE_START = re.compile(r'^([-+*>]|#{1,6}|[0-9]{1,9}[.)])$')   # a word that would start a list or heading


def wrap(text):
    """text with every prose line over WIDTH folded at spaces (a list item's rest indented by two); code blocks,
    table rows, headings and comments stay whole, and no folded line starts with a list or heading marker."""
    out, code = [], False
    for line in text.split('\n'):
        code ^= line.startswith('```')
        if code or len(line) <= WIDTH or line.startswith(('|', '#', '<!--', '```')):
            out.append(line)
            continue
        indent = '  ' if line.startswith('- ') else ''
        row = ''
        for word in line.split(' '):
            if row.strip() and len(row) + 1 + len(word) > WIDTH:
                head, _, last = row.rpartition(' ')
                if LINE_START.match(word) and head.strip():
                    out.append(head)
                    row = indent + last + ' ' + word
                else:
                    out.append(row)
                    row = indent + word
            else:
                row = row + ' ' + word if row else word
        out.append(row)
    return '\n'.join(out)
