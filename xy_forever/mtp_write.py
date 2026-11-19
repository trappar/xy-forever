"""MTP writes for host tools (docs/SAFETY.md, "Host access"): make a folder, upload a file, delete an object.

Writer is mtp_read.Session plus three write operations; mtp_read.Session itself stays read only. Every write
is checked before its command reaches the transport: the path is below /yaffs2 (the OP-XY's MTP storage root:
device path /yaffs2/a/b is MTP path /a/b), never /yaffs2/bootloader.dev nor /yaffs2/upgrade or below it (ASCII
case ignored), no file more than 6 folders below /yaffs2 (#124, xy_forever/depth.py), and a delete only inside
the scratch folder named when the Writer is made. The stock OS's MTP shows its /fat32 at the root instead: the
same checks, stricter than it needs. Works over any mtp_read transport (USB, the simulator's diag.mtp tunnel in
scripts/throughput.py, a fake in the tests).
"""
import struct

from xy_forever import depth
from xy_forever.mtp_read import FOLDER, OK, ROOT, MtpError, Refused, Session

WRITES = {0x100B: 'DeleteObject', 0x100C: 'SendObjectInfo', 0x100D: 'SendObject'}
FILE = 0x3000                   # ObjectFormat Undefined
GENERIC_FOLDER = 1              # AssociationType
PACKET = 512                    # high-speed bulk packet: a data phase of a multiple of it ends with a ZLP
OUT_CHUNK = 0x10000             # bytes per bulk-OUT transfer (a multiple of PACKET)
STORAGE_ROOT = '/yaffs2'


def problem(path, folder=False, deep=True):
    """None when a device path may be written (made, replaced; deep False: deleted), else why not."""
    if not path.startswith(STORAGE_ROOT + '/'):
        return '%s is not below %s (the MTP storage)' % (path, STORAGE_ROOT)
    parts = path[len(STORAGE_ROOT) + 1:].split('/')
    if not all(p and p not in ('.', '..') for p in parts):
        return '%s: an empty, "." or ".." name' % path
    first = parts[0].lower()
    if first == 'upgrade' or (len(parts) == 1 and first == 'bootloader.dev'):
        return '%s is protected (/yaffs2/bootloader.dev and /yaffs2/upgrade/ are never changed)' % path
    why = depth.refusal(path, folder) if deep else None
    return why and why[len('refused: '):]


def ptp_string(text):
    if not text:
        return b'\0'
    units = (text + '\0').encode('utf-16-le')
    return bytes([len(units) // 2]) + units


def object_info(name, size, folder):
    """The ObjectInfo dataset of SendObjectInfo (storage and parent 0: they travel as the command's params)."""
    return (struct.pack('<IHHIHIIIIIIIHII', 0, FOLDER if folder else FILE, 0, 0 if folder else size,
                        0, 0, 0, 0, 0, 0, 0, 0, GENERIC_FOLDER if folder else 0, 0, 0)
            + ptp_string(name) + b'\0\0\0')


class Writer(Session):
    def __init__(self, transport, scratch):
        """scratch: the device folder (below /yaffs2) that deletes are confined to."""
        scratch = scratch.rstrip('/')
        why = problem(scratch, folder=True)
        if why:
            raise Refused('scratch folder refused: ' + why)
        super().__init__(transport)
        self.scratch = scratch

    def refuse(self, path, folder=False, deep=True):
        why = problem(path, folder, deep)
        if why:
            raise Refused('refused: ' + why)

    def locate(self, path):
        """(storage, handle) of a device path below /yaffs2 (handle ROOT for /yaffs2 itself), or None."""
        storages = self.storages()
        if not storages:
            raise MtpError('no MTP storage')
        if path == STORAGE_ROOT:
            return storages[0], ROOT
        for storage in storages:
            parent = ROOT
            for part in path[len(STORAGE_ROOT) + 1:].split('/'):
                match = [c for c in self.children(storage, parent) if c[1] == part]
                if not match:
                    break
                parent = match[0][0]
            else:
                return storage, parent
        return None

    def _exchange(self, code, params, data=None):
        """One write transaction (the caller checked the path): (response code, response params)."""
        name = WRITES[code]
        self._command(name, code, params)
        if data is not None:
            stream = memoryview(struct.pack('<IHHI', 12 + len(data), 2, code, self.tid) + data)
            for at in range(0, len(stream), OUT_CHUNK):
                rc = self.transport.write(bytes(stream[at:at + OUT_CHUNK]))
                if rc:
                    raise MtpError('%s: data stopped at %d/%d bytes (libusb %d)' % (name, at, len(stream), rc))
            if len(stream) % PACKET == 0:
                self.transport.write(b'')
        rcode, _ = self._response(name)
        return rcode, self.answer

    def create(self, path, data=None, where=None):
        """A folder (data None) or a file at a device path: (storage, handle). The parent folder must exist;
        where: its (storage, handle) when the caller knows it (then the name must be new in it)."""
        folder = data is None
        self.refuse(path, folder)
        parent_path, name = path.rsplit('/', 1)
        where = where or self.locate(parent_path)
        if where is None:
            raise MtpError('%s: no folder %s' % (path, parent_path))
        storage, parent = where
        rcode, answer = self._exchange(0x100C, (storage, parent), object_info(name, len(data or b''), folder))
        if rcode != OK or len(answer) < 3:
            raise MtpError('SendObjectInfo %s: response 0x%04x' % (path, rcode))
        if not folder:
            rcode, _ = self._exchange(0x100D, (), data)
            if rcode != OK:
                raise MtpError('SendObject %s: response 0x%04x' % (path, rcode))
        return storage, answer[2]

    def mkdir(self, path):
        """Make a folder and any missing folders above it (each checked): (storage, handle)."""
        path = path.rstrip('/')
        self.refuse(path, folder=True)
        made, where = STORAGE_ROOT, None
        for part in path[len(STORAGE_ROOT) + 1:].split('/'):
            made += '/' + part
            where = self.locate(made) or self.create(made)
        return where

    def put(self, path, data):
        """Upload a file (bytes) to a device path: (storage, handle). A file already there is replaced only
        inside the scratch folder (deleted first); elsewhere it is refused."""
        self.refuse(path)
        found = self.locate(path)
        if found is not None:
            self.delete(path, found[1])
        return self.create(path, data)

    def inside_scratch(self, path):
        return path == self.scratch or path.startswith(self.scratch + '/')

    def delete(self, path, handle=None):
        """Delete one object (a folder: as the device does it; XY Forever's MTP needs it empty), only inside
        the scratch folder; handle: its handle when the caller knows it. False when it is not there."""
        path = path.rstrip('/')
        if not self.inside_scratch(path):
            raise Refused('refused: %s is outside the scratch folder %s' % (path, self.scratch))
        self.refuse(path, deep=False)
        if handle is None:
            found = self.locate(path)
            if found is None:
                return False
            handle = found[1]
        rcode, _ = self._exchange(0x100B, (handle,))
        if rcode != OK:
            raise MtpError('DeleteObject %s: response 0x%04x' % (path, rcode))
        return True

    def remove_tree(self, path=None, where=None):
        """Delete a folder inside the scratch folder (default: the scratch folder itself) and everything in it,
        deepest first; the device paths removed. where: its (storage, handle) when known."""
        path = (path or self.scratch).rstrip('/')
        if not self.inside_scratch(path):
            raise Refused('refused: %s is outside the scratch folder %s' % (path, self.scratch))
        self.refuse(path, deep=False)
        where = where or self.locate(path)
        if where is None:
            return []
        removed = []
        for handle, name, fmt, _ in self.children(*where):
            if fmt == FOLDER:
                removed += self.remove_tree(path + '/' + name, (where[0], handle))
            elif self.delete(path + '/' + name, handle):
                removed.append(path + '/' + name)
        if self.delete(path, where[1]):
            removed.append(path)
        return removed
