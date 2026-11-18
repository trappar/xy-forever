"""Test025 file boot on both processors: layout, decoder, buffers and agreement rules; no device execution."""
from pathlib import Path
import re
import struct
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever.source_text import unit_text  # noqa: E402
import xyf_build as builder
from xy_forever.census import (FILE_BOOT_BYTES, FILE_BOOT_FIELDS, FILE_BOOT_MAGIC, PEER_DIAGNOSTIC_FIELDS,
                                   PEER_SNAPSHOT_WORDS, STORAGE_DIAGNOSTIC_WORDS, file_boot)

HEADER = (ROOT / 'target/file_boot.hpp').read_text()
SOURCE = unit_text(ROOT / 'target/file_boot.cpp')
SESSION = (ROOT / 'target/peer_services.cpp').read_text()
COLD = (ROOT / 'target/cold.cpp').read_text()
BOOT_HEADER = (ROOT / 'target/xyf_boot.hpp').read_text()


def constant(name, header=HEADER):
    return int(re.search(r'\b' + name + r' = (0x[0-9a-f]+|\d+)u?[;,]', header).group(1), 0)


def body(text, start):
    begin = text.index(start)
    return text[begin:text.index('\n}\n', begin)]


class FileBootTest(unittest.TestCase):
    def test_header_layout_matches_decoder(self):
        struct_text = HEADER[HEADER.index('struct Result {'):HEADER.index('};', HEADER.index('struct Result {'))]
        names = []
        for decl in re.findall(r'uint32_t ([^;]+);', struct_text):
            for name in decl.split(','):
                name = name.strip()
                if not name.startswith('digest['):
                    names.append(name)
        self.assertEqual(names, FILE_BOOT_FIELDS)
        self.assertIn('uint32_t digest[8];', struct_text)
        storage = (ROOT / 'target/cold_storage.hpp').read_text()
        snapshot = storage[storage.index('struct Snapshot {'):storage.index('};', storage.index('struct Snapshot {'))]
        fields = sum(len(d.split(',')) + (int(re.search(r'\[(\d+)\]', d).group(1)) - 1 if '[' in d else 0)
                     for d in re.findall(r'U32 ([^;]+);', snapshot))
        self.assertEqual(6 + 2 * fields, STORAGE_DIAGNOSTIC_WORDS)
        self.assertEqual(FILE_BOOT_BYTES, 4 * (36 + STORAGE_DIAGNOSTIC_WORDS + len(PEER_DIAGNOSTIC_FIELDS) +
                                               2 * PEER_SNAPSHOT_WORDS))

    def test_decoder_reads_outcome_digest_and_diagnostics(self):
        words = [0] * (FILE_BOOT_BYTES // 4)
        values = dict(magic=FILE_BOOT_MAGIC, version=1, role=1, outcome=1, committed=1, image_bytes=3488536,
                      load_reason=11, admit_reason=0, begin_result=1, poll_result=4, polls=77)
        for name, value in values.items():
            words[FILE_BOOT_FIELDS.index(name)] = value
        words[28:36] = struct.unpack('<8I', bytes(range(32)))
        block = struct.pack('<%dI' % len(words), *words)
        regions = [dict(address=0x8ab06000, bytes=len(block), offset=0)]
        out = file_boot(regions, block)
        self.assertEqual((out['outcome'], out['committed'], out['image_bytes']), ('file', 1, 3488536))
        self.assertEqual((out['load_reason'], out['admit_reason']), ('not_run', 'ok'))
        self.assertEqual((out['begin_result'], out['poll_result']), ('waiting', 'ready'))
        self.assertEqual(out['sha256'], bytes(range(32)).hex())
        self.assertIn('before', out['diagnostic'])
        self.assertIsNone(file_boot([dict(address=0, bytes=16, offset=0)], bytes(16)))

    def test_buffers_avoid_heap_arena_module_embedded_source_and_framebuffer(self):
        staging, size = constant('staging'), constant('staging_bytes')
        scratch, scratch_size = constant('scratch'), constant('scratch_bytes')
        boot = constant('boot_buffer')
        self.assertEqual((staging + size, scratch + scratch_size <= staging), (0x8c000000, True))
        for lo, hi in ((0x88a65bf8, 0x8aa65bf4), (0x8ab00000, 0x8ab10000), (0x8b000000, 0x8b800000),
                       (0x8c000000, 0x8c03c000)):
            for begin, end in ((staging, staging + size), (scratch, scratch + scratch_size)):
                self.assertTrue(end <= lo or begin >= hi, (hex(begin), hex(lo)))
        self.assertEqual(boot, 0x8a000000)                              # stock RAM-boot source
        self.assertLessEqual(boot + size - 512, 0x8a800000)             # largest copy stays below the module area
        self.assertEqual(builder.FILE_STAGING, (scratch, staging + size))
        self.assertIn('FILE_STAGING', builder.check_boot_reservations.__code__.co_names)

    def test_file_is_booted_only_after_commit_and_exact_copy(self):
        main = body(SOURCE, 'extern "C" void xy_forever_file_boot() {')
        self.assertIn('xy_forever_storage_image_loaded = 0;', main)
        self.assertEqual(SOURCE.count('xy_forever_storage_image_loaded = 1'), 1)
        self.assertIn('if (committed) {\n        if (hand_off(bytes, crc)) { xy_forever_storage_image_loaded = 1;',
                      main)
        hand_off = body(SOURCE, 'bool hand_off(U32 bytes, U32 crc) {')
        self.assertLess(hand_off.index('if (r.copy_crc != crc) return false;'),
                        hand_off.index('xy_forever_storage_image_bytes = bytes;'))
        # cold.cpp re-admits the handed-off image by CRC and LDR policy before RAM boot.
        self.assertIn('xy_forever_storage_image_crc &&', COLD)
        self.assertIn('xy_forever_file_boot();', COLD)

    def test_same_admission_on_both_processors_before_agreement(self):
        admit = body(SOURCE, 'U32 admit(Byte* image, U32 bytes) {')
        for check in ('xyf_core::inspect(', 'summary.entry != 0x11a00000u', 'trace_slot::replace(',
                      'trace_slot::locate_zero('):
            self.assertIn(check, admit)
        # a (role 0): an image is admitted before it is hashed and frozen for b
        # (in a_prepare for the CHECK-before-BOOT flow, inline otherwise).
        self.assertIn('r.admit_reason = admit(', body(SOURCE, 'U32 a_prepare('))
        owner = body(SOURCE, 'bool role0_menu_boot(U32& bytes, U32& crc) {')
        self.assertLess(owner.index('a_prepare(storage, mounted, lib, index, bytes, crc)'), owner.index('session.freeze_image('))
        inline = owner[owner.index('#else\n        if (item != xyf_boot::item_boot_default'):]
        self.assertLess(inline.index('r.admit_reason = admit('), inline.index('session.freeze_image('))
        # b (role 1): every received image is admitted before the session may release.
        client = body(SOURCE, 'bool role1_menu_boot(U32& bytes) {')
        parts = client.split('session.admit(received)')
        self.assertEqual(len(parts), 3)                      # two receive loops
        for part in parts[:2]:
            self.assertIn('r.admit_reason = admit(', part)
            self.assertIn('if (r.admit_reason != reason_ok) break;', part)

    def test_owner_refuses_only_without_data_body_and_before_release(self):
        poll = body(SESSION, 'Result Stock121PeerSession::poll(U32 now) {')
        self.assertIn('if(req.operation!=0x18)return fail(Result::protocol);\n                    declined_=true;respond(1);',
                      poll)
        self.assertIn('status==xyf_core::peer::Status::identity&&req.operation==0x18&&!req.argument0){\n'
                      '                    declined_=true;respond(2);', poll)
        # Release after a refusal is agreement on the embedded image.
        # (Staged menu boot: a release without a restored image also agrees on embedded.)
        self.assertIn('if(declined()||(staged_&&server_.phase()!=xyf_core::peer::Phase::release)){\n'
                      '                agreed_=true;', poll)
        committed = body(SESSION, 'bool Stock121PeerSession::committed() const {')
        self.assertIn('server_.phase()==xyf_core::peer::Phase::release&&byte(primary_)==1', committed)
        self.assertIn('else if(client_.ready())committed_=true;', poll)

    def test_timeouts_cover_owner_storage_work(self):
        self.assertLess(constant('role0_idle_ms'), constant('role0_cap_ms', BOOT_HEADER))
        # b enters ~0.53 s before a (Test023); a mounts (up to ~4.6 s seen), reads and hashes first.
        self.assertGreaterEqual(constant('role1_boot_cap_ms', BOOT_HEADER), 20000)

    def test_builder_mode_and_reports(self):
        self.assertTrue(builder.features('xyf-product')['file_boot'])
        self.assertEqual(builder.FILE_VARIANT_REPORTS['xyf-product'][:6], 'pfile-')
        self.assertNotIn('11a00000', ''.join(sorted(builder.STORAGE_CALLS)))


if __name__ == '__main__':
    unittest.main()
