"""Generated protocol documentation tree layout (split leaves before their budget grows)."""
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
    (0x49, 0x7f, 'cmd-storage.md', 'package and storage reports'),
)
DOC_LEAVES = tuple('%s/%s' % (DOC_DIR, f) for f in (
    ['transport.md', 'rules.md', 'commands.md'] + [leaf[2] for leaf in CMD_LEAVES] + ['examples.md', 'events-dev.md']))
