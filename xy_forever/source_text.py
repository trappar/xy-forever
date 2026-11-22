"""A source file as one text with its included parts inlined.

target/usb_service.cpp includes its parts (usb_service_*.ipp) where their text was. Tests that read
the file as text read unit_text(path): the same text as before the split, whatever the parts are.
"""

import re
from pathlib import Path

PART = re.compile(r'^#include "(\w+\.ipp)".*\n', re.M)
HEADER = '// Part of '  # each part's first line says what it is part of


def unit_text(path):
    """The file's text, each #include of a .ipp part (beside it) replaced by that part less its header line."""
    path = Path(path)

    def part(match):
        text = (path.parent / match.group(1)).read_text()
        return text.split('\n', 1)[1] if text.startswith(HEADER) else text

    return PART.sub(part, path.read_text())
