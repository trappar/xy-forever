"""XY Forever's release version: the one place it is set. Bumped only in a release pull request.

A build is named by its image (the first 8 hex digits of its candidate's SHA-256, `builds/<image>/`);
every build between two releases carries the same version. The firmware gets it as XYF_VERSION (text,
hello `version=`, the menu) and XYF_VERSION_CODE (a number: stage 2's min_stage1, reports).
"""

VERSION = '0.1.0'


def code(version=VERSION):
    """MAJOR << 16 | MINOR << 8 | PATCH (each 0..255): 0.1.0 is 0x100, above every old build number."""
    parts = [int(p) for p in version.split('.')]
    if len(parts) != 3 or not all(0 <= p <= 255 for p in parts):
        raise ValueError('release version must be MAJOR.MINOR.PATCH, each 0..255: %r' % version)
    return parts[0] << 16 | parts[1] << 8 | parts[2]


def defines():
    """The compiler defines that carry it."""
    return ('-DXYF_VERSION="%s"' % VERSION, '-DXYF_VERSION_CODE=%#xu' % code())
