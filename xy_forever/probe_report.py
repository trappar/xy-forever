"""Decode the bounded, checksummed Test 002 diagnostic. No device access."""
import argparse
import json
from pathlib import Path
import struct
import zlib

SNAPSHOT_FIELDS = (
    'role stock_mounted local_mounted initialized references handle '
    'interrupt_mask source_control vector_index vector_handler vector_context '
    'rsi_control rsi_mask rsi_status rsi_configuration rsi_clock '
    'completion completion_initialized completion_count '
    'adma_control adma_pointer transfer_address transfer_bytes transfer_error '
    'log_callback0 log_callback1 log_callback2 log_callback3 device mounted readonly'
).split()
RESULTS = ('ok unavailable identity role lifecycle irq device busy activate mount '
           'write_attempt open_handles unmount shutdown').split()
READ_RESULTS = ('ok unavailable path open seek size range read close directory capacity '
                'duplicate display selection').split()
STAGES = 'idle captured guarded activated mounted closed failed'.split()
HEAP_RESULTS = 'ok unavailable read layout staging_overlap free_list cursor capacity'.split()
LOOKUP_RESULTS = ('ok unavailable argument read pointer links cycle capacity name '
                  'duplicate missing heap placement').split()
LOOKUP_FIELDS = 'result visited name_bytes reads offending_address sentinel_next sentinel_prev device'.split()
SHAPE_FIELDS = ('name flags4 chunk_bytes chunks_per_block start_block end_block f1c f20 f28 f30 '
                'is_yaffs2 f3c f7c write_chunk read_chunk erase mark_bad deinit init f98 '
                'write_tags read_tags query_block tag_mark_bad remove_obj f58 mounted readonly').split()
# Mirrors target/construct_spans.hpp (a test keeps them equal).
CONSTRUCT_SPANS = (('yaffs_configure_device', 0x889f963e, 0xc2), ('calloc', 0x0803e06c, 0x18),
                   ('heap_block', 0x88a2ec6c, 0x5fc), ('yaffs_register_device', 0x889fb24e, 0x6c),
                   ('list_insert', 0x889f9ad2, 0x10))


def construct_chunks():
    for name, address, length in CONSTRUCT_SPANS:
        for offset in range(0, length, 16):
            yield name, address + offset, min(16, length - offset)


# Exact stock 1.2.1 os.ldr (SHA-256 60358afd...): size and CRC32 only.
STOCK121_OS = (2373336, 0x89d08ddd)
HEAP_FIELDS = 'context count base extent brk next_context cursor free_head free_nodes highest_free_end'.split()


def report_only_fixture():
    """Known writer input for Test004; contains no cold hardware observation."""
    words = [0] * 90
    words[:3] = [0x32525058, 1, 360]
    words[4:8] = [1, 1, 1, 1]
    words[11:13] = [0x53444358, 1]
    words[14] = words[79] = 1
    raw = struct.pack('<90I', *words)
    words[3] = zlib.crc32(raw)
    return struct.pack('<90I', *words)


def transport_stamp_fixture():
    """Cold-generated transport stamp, diagnostic version2, without hardware data."""
    raw = bytearray(report_only_fixture())
    struct.pack_into('<I', raw, 12, 0)
    struct.pack_into('<I', raw, 48, 2)
    struct.pack_into('<I', raw, 12, zlib.crc32(raw))
    return bytes(raw)


def decode(data):
    if len(data) != 360:
        raise ValueError('Expected a complete 360-byte Test 002 report')
    words = struct.unpack('<90I', data)
    if words[:3] != (0x32525058, 1, 360):
        raise ValueError('Report magic/version/length differs')
    if words[3] != zlib.crc32(data[:12] + bytes(4) + data[16:]):
        raise ValueError('Report checksum differs')
    def label(table, value):
        return table[value] if value < len(table) else 'unknown:' + str(value)
    result = dict(begin=label(RESULTS, words[4]), read=label(READ_RESULTS, words[5]),
                  cleanup=label(RESULTS, words[6]), safe_handoff=bool(words[7]),
                  marker_size=words[8], marker_read=words[9], marker_mismatches=words[10],
                  diagnostic=dict(magic=hex(words[11]), version=words[12],
                                  stage=label(STAGES, words[13]), result=label(RESULTS, words[14]),
                                  denied_writes=words[15], handles=words[16]),
                  before=dict(zip(SNAPSHOT_FIELDS, words[17:48])),
                  after=dict(zip(SNAPSHOT_FIELDS, words[48:79])),
                  heap_result=label(HEAP_RESULTS, words[79]),
                  heap=dict(zip(HEAP_FIELDS, words[80:90])))
    if words[12] in (7, 8, 9, 10):
        # Version7 (Test013): fresh mount plus direct YAFFS I/O. The after block
        # is an I/O record (see target/fresh_mount_probe.cpp).
        io = words[48:79]
        signed = lambda v: v - (1 << 32) if v & 0x80000000 else v
        def listing(at):
            return dict(opened=bool(io[at]), entries=io[at + 1] & 0xffff,
                        regular_files=io[at + 1] >> 16, closedir=signed(io[at + 2]))
        names = struct.pack('<12I', *io[16:28]) if words[12] == 7 else struct.pack('<8I', *io[16:24])
        del result['after']
        result['fresh_io'] = dict(
            mount_ms=round(io[0] * 1024 / 400000.0, 1), stack_used_bytes=io[1],
            construct_mismatch=('not checked' if io[2] == 0xffffffff else
                                [SHAPE_FIELDS[i] for i in range(len(SHAPE_FIELDS)) if io[2] >> i & 1]),
            direct_gate=bool(io[3] & 1), direct_ready=bool(io[3] & 2),
            marker_fd=signed(io[4]), marker_read=signed(io[5]), marker_eof_read=signed(io[6]),
            marker_close=signed(io[7]), error_after_file=signed(io[8]),
            images=listing(9), root=listing(12), error_after_dirs=signed(io[15]),
            root_first_names=[names[i:i + 16].split(b'\0')[0].decode('ascii', 'replace')
                              for i in range(0, len(names), 16) if names[i]],
            rsi_status_after_mount=hex(io[28]), xy_forever_entries=io[29] & 0xffff,
            device=hex(result['before']['device']), denied_writes=words[15],
            heap_brk_after=hex(words[84]))
        if words[12] in (9, 10):
            status, verdict = io[27], io[30]
            reasons = {0: 'matched', 1: 'open failed', 2: 'size differs/too large', 3: 'CRC differs',
                       4: 'heap too close before load', 5: 'heap too close after load',
                       6: 'read/close error', 7: 'session or heap refusal',
                       8: 'LDR policy refused', 9: 'entry differs', 10: 'no exact report slot'}
            result['fresh_io']['storage_boot'] = dict(
                bytes=io[24], crc32=hex(io[25]), ms=round(io[26] * 1024 / 400000.0, 1),
                fd=None if status == 0xffffffff else status & 0xffff,
                probe_loaded=bool(verdict & 1),
                booted_from={1: 'storage file', 2: 'embedded fallback',
                             3: 'embedded after storage patch refusal'}.get((verdict >> 8) & 0xff, 'unknown'),
                reason=reasons.get(verdict >> 16, 'unknown:%d' % (verdict >> 16)))
        if words[12] == 8:
            status = io[27]
            result['fresh_io']['stock_payload'] = dict(
                bytes=io[24], crc32=hex(io[25]), ms=round(io[26] * 1024 / 400000.0, 1),
                fd=None if status == 0xffffffff else status & 0xffff,
                last_read=None if status == 0xffffffff else ((status >> 16) & 0xff) - (256 if status >> 23 & 1 else 0),
                close=None if status == 0xffffffff else (status >> 24) - (256 if status >> 31 else 0),
                matches_stock121=(io[24], io[25]) == STOCK121_OS)
        result['fresh_io']['passed'] = (
            words[4:8] == (0, 0, 0, 1) and words[8:11] == (512, 512, 0) and words[13] == 5 and
            words[15:17] == (0, 0) and words[79] == 0 and io[5] == 512 and io[6] == 0 and
            io[7] == 0 and ((io[10] & 0xffff) >= 1 if words[12] in (9, 10) else (io[10] & 0xffff) == {7: 2, 8: 3}[words[12]]) and io[11] == 0 and
            io[14] == 0 and (words[12] == 7 or (words[12] == 8 and (io[24], io[25]) == STOCK121_OS) or
                             (words[12] in (9, 10) and io[30] & 0xffff00ff == 1 and (io[30] >> 8) & 0xff == 1)))
    if words[12] == 6:
        # Version6 (Test012): fresh construct, write-denied read-only mount,
        # marker read, image listing, unmount. marker_size high half is the
        # mount return code; after.log_callback0..3 carry extras.
        after = result['after']
        mount_status = words[8] >> 16
        catalog = after['log_callback2']
        result['marker_size'] = words[8] & 0xffff
        result['fresh_mount'] = dict(
            mount_status=mount_status - 0x10000 if mount_status & 0x8000 else mount_status,
            mount_ms=round(after['log_callback0'] * 1024 / 400000.0, 1),
            stack_used_bytes=after['log_callback1'],
            images_listed=catalog & 0xffff if catalog != 0xffff0000 else None,
            list_result=label(READ_RESULTS, catalog >> 16) if catalog != 0xffff0000 else 'not attempted',
            construct_mismatch=('not checked' if after['log_callback3'] == 0xffffffff else
                                [SHAPE_FIELDS[i] for i in range(len(SHAPE_FIELDS))
                                 if after['log_callback3'] >> i & 1]),
            device=hex(result['before']['device']),
            denied_writes=words[15],
            heap_brk_after=hex(words[84]))
        for key in ('log_callback0', 'log_callback1', 'log_callback2', 'log_callback3'):
            del after[key]
        result['fresh_mount']['passed'] = (
            words[4:8] == (0, 0, 0, 1) and (words[8] & 0xffff) == 512 and words[9:11] == (512, 0) and
            words[13] == 5 and words[15:17] == (0, 0) and words[79] == 0 and mount_status == 0 and
            catalog == 2)
    if words[12] in (4, 5):
        # Version4 (Test010): RAM-only stock device construction. 'before' is the
        # pre-construction state with device set to the constructed address;
        # the after block is the post-construction lookup trace plus shape mask
        # and pre-construction break; the heap block is post-construction.
        if any(words[58:79] if words[12] == 4 else words[58:59]):
            raise ValueError('Construct trace has unexpected trailing data')
        trace = dict(zip(LOOKUP_FIELDS, words[48:56]))
        trace['result'] = label(LOOKUP_RESULTS, trace['result'])
        for key in ('offending_address', 'sentinel_next', 'sentinel_prev', 'device'):
            trace[key] = hex(trace[key])
        mask = words[56]
        del result['after']
        result['construct'] = dict(
            result=label(RESULTS, words[14]), device=hex(words[17 + 28]),
            lookup_after=trace,
            shape_mismatch=('not checked' if mask == 0xffffffff else
                            [SHAPE_FIELDS[i] for i in range(len(SHAPE_FIELDS)) if mask >> i & 1]),
            heap_brk_before=hex(words[57]), heap_brk_after=hex(words[84]),
            registered=(words[14] == 0 and trace['result'] == 'ok' and trace['visited'] == 1 and
                        words[48 + 7] == words[17 + 28] != 0 and mask == 0))
        if words[12] == 5:
            bits = sum(words[59 + i] << (32 * i) for i in range(4))
            chunks = list(construct_chunks())
            if bits >> len(chunks):
                raise ValueError('Span mask exceeds compared chunks')
            raw = struct.pack('<16I', *words[63:79])
            differing = [chunks[i] for i in range(len(chunks)) if bits >> i & 1]
            result['construct']['span_mismatch'] = [
                dict(span=name, address=hex(address),
                     observed=raw[16 * n:16 * n + size].hex() if n < 4 else None,
                     note=('contains clock global 0x88a2ef28 (runtime variable; outside gate)'
                           if address <= 0x88a2ef28 < address + size else None))
                for n, (name, address, size) in enumerate(differing)]
    if words[12] == 3:
        # Version3 reuses the unused after-snapshot for a bounded lookup trace.
        if any(words[56:79]):
            raise ValueError('Lookup trace has unexpected trailing data')
        trace = dict(zip(LOOKUP_FIELDS, words[48:56]))
        trace['result'] = label(LOOKUP_RESULTS, trace['result'])
        for key in ('offending_address', 'sentinel_next', 'sentinel_prev', 'device'):
            trace[key] = hex(trace[key])
        del result['after']
        result['lookup'] = trace
    if words[11] != 0x53444358 or words[12] not in tuple(range(1, 12)) or words[7] not in (0, 1):
        raise ValueError('Diagnostic ABI or handoff flag differs')
    result['probe_passed'] = (words[12] == 1 and words[4:11] == (0, 0, 0, 1, 512, 512, 0) and
                              words[13:17] == (5, 0, 0, 0) and words[79] == 0 and
                              result['before']['role'] == 0)
    result['writer_fixture_matched'] = data == report_only_fixture()
    result['transport_stamp_matched'] = data == transport_stamp_fixture()
    if words[12] == 2 and not result['transport_stamp_matched']:
        raise ValueError('Malformed transport-only diagnostic')
    result['cold_snapshot_captured'] = (words[12] in (1, 3) and words[5:11] == (1, 1, 1, 0, 0, 0) and
                                        words[13] == 1 and words[4] == words[14] and
                                        words[15:17] == (0, 0) and words[79] == 0)
    result['report_origin'] = ('cold-generated transport stamp; no hardware snapshot'
                               if result['transport_stamp_matched'] else
                               'Test004 preloaded writer fixture; no hardware snapshot'
                               if result['writer_fixture_matched'] else
                               'cold snapshot; no storage activation' if result['cold_snapshot_captured'] else
                               'cold probe record')
    result['limits'] = ('Transport only; no retained heap/controller/IRQ reads or storage test'
                        if result['transport_stamp_matched'] else
                        'Postboot writer fixture only; no cold hardware capture or storage test'
                        if result['writer_fixture_matched'] else
                        'Snapshot only; readiness is not proof of storage initialization/mount progress'
                        if result['cold_snapshot_captured'] else
                        'Cold file read only; no chosen-image, companion programming or recovery qualification')
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('report', type=Path)
    args = parser.parse_args()
    print(json.dumps(decode(args.report.read_bytes()), indent=2))


if __name__ == '__main__':
    main()
