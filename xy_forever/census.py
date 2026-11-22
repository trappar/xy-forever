"""Decode a census file (a device report: `uv run -m xy_forever.census REPORT.bin`): the 360-byte report, the
cold memory census and the result blocks after it (key UI, link probe, file boot). Offline only."""

import argparse
import json
import struct
import zlib
from pathlib import Path

from .census_usb import usb_service
from .probe_report import decode

MAGIC = 0x31534E43
HEADER = 64


def parse(data):
    if len(data) < 360 + HEADER:
        raise ValueError('Census file too short')
    report = decode(data[:360])
    if report['diagnostic']['version'] != 11:
        raise ValueError('Not a census report')
    dump = data[360:]
    words = struct.unpack_from('<11I', dump)
    if words[0] != MAGIC or words[1] != 1 or words[2] != len(dump) or words[3] > 32:  # 16, product 24 (Test 043)
        raise ValueError('Census header differs')
    if words[9] != zlib.crc32(dump[HEADER:]):
        raise ValueError('Census CRC differs')
    regions = []
    for i in range(words[3]):
        address, size, offset = struct.unpack_from('<3I', dump, HEADER + 12 * i)
        if offset + size > len(dump):
            raise ValueError('Census region out of range')
        regions.append(dict(address=address, bytes=size, offset=offset))
    header = dict(
        role=words[4],
        gpio=hex(words[5]),
        cycles=words[6] | words[7] << 32,
        heap_brk=hex(words[8]),
        heap_result=words[10],
    )
    return report, header, regions, dump


def read(regions, dump, address, size=4):
    for r in regions:
        if r['address'] <= address and address + size <= r['address'] + r['bytes']:
            at = r['offset'] + address - r['address']
            return dump[at : at + size]
    return None


# 1.2.1 bootloader key enum (pointer table 0x88a25584); bit i of the PSoC
# key bitmap (frame type 0x42) is key i.
KEYS = (
    'Help Tempo Synth Drum Tape Mixer Track1 Track2 Track3 Track4 EncClick1 EncClick2 '
    'EncClick3 EncClick4 Sound1 Sound2 Sound3 Sound4 Sound5 Sound6 Sound7 Sound8 Seq Mic Usb '
    'Bar F3 F3Sharp G3 G3Sharp A3 A3Sharp B3 C4 C4Sharp D4 D4Sharp E4 F4 F4Sharp G4 G4Sharp '
    'A4 A4Sharp B4 C5 C5Sharp D5 D5Sharp E5 Rec Play Stop Left Right Shift Step1 Step2 Step3 '
    'Step4 Step5 Step6 Step7 Step8 Step9 Step10 Step11 Step12 Step13 Step14 Step15 Step16'
).split()
KEY_UI_MAGIC = 0x3149554B
KEY_UI_FIELDS = (
    'magic version role stages frame_header bitmap0 bitmap1 bitmap2 play_snapshot '
    'identity_mismatch framebuffer start_cycles display_ms link_ms link_state polls '
    'events exit_reason end_ms heap_brk_before heap_brk_after heap_results last_event '
    'stack_used'
).split()
# Version 3 (Test020) adds synchronous display fields; events drop from 24 to 20.
KEY_UI_DISPLAY_FIELDS = ['dma_stat_entry', 'display_entry', 'presents', 'present_skips']
STAGES = ('snapshot', 'play', 'identity', 'display', 'displayed', 'link', 'greeted', 'closed')
EXITS = ('none', 'play', 'timeout', 'link', 'identity', 'role', 'no_play', 'snapshot')


def key_name(index):
    return KEYS[index] if index < len(KEYS) else '#%d' % index


def held_keys(bitmap_bytes):
    return [key_name(i) for i in range(len(bitmap_bytes) * 8) if bitmap_bytes[i // 8] >> (i % 8) & 1]


def boot_key_snapshot(regions, dump):
    """Stock key status reply (request 0x41) left at 0x8807ce7e by the bootloader."""
    frame = read(regions, dump, 0x8807CE7E, 14)
    if not frame or frame[2] != 0x42 or frame[3] != 0x0A:
        return None
    return held_keys(frame[4:14])


def key_ui(regions, dump):
    """Test018-020 key UI result block, or None (census-only files)."""
    for r in regions:
        block = dump[r['offset'] : r['offset'] + r['bytes']]
        if r['bytes'] != 0xC0 or struct.unpack_from('<I', block)[0] != KEY_UI_MAGIC:
            continue
        words = struct.unpack_from('<48I', block)
        fields = KEY_UI_FIELDS + (KEY_UI_DISPLAY_FIELDS if words[1] >= 3 else [])
        out = dict(zip(fields, words))
        first, slots = len(fields), 48 - len(fields)
        frame = struct.pack('<4I', out['frame_header'], out['bitmap0'], out['bitmap1'], out['bitmap2'])
        out['frame_type'], out['frame_length'] = hex(frame[2]), frame[3]
        out['snapshot_keys'] = held_keys(frame[4:14])
        out['stages'] = [n for i, n in enumerate(STAGES) if out['stages'] >> i & 1]
        out['exit_reason'] = EXITS[out['exit_reason']] if out['exit_reason'] < len(EXITS) else out['exit_reason']
        out['link_open'], out['link_greeted'] = out['link_state'] & 0xFF, out['link_state'] >> 8 & 0xFF
        out['event_log'] = [
            '%s %s @%dms' % (key_name(e & 0xFF), 'down' if e >> 8 & 1 else 'up', e >> 16)
            for e in words[first : first + min(out['events'], slots)]
        ]
        if 'present_skips' in out:
            out['smc_dma_active_entry'], out['frame_flag_entry'] = (
                out['display_entry'] & 0xFF,
                out['display_entry'] >> 8 & 0xFF,
            )
            out['dma_not_idle'], out['stale_busy_cleared'] = out['present_skips'] & 0xFFFF, out['present_skips'] >> 16
            out['dma_stat_entry'] = hex(out['dma_stat_entry'])
        for k in (
            'framebuffer',
            'heap_brk_before',
            'heap_brk_after',
            'identity_mismatch',
            'heap_results',
            'magic',
            'frame_header',
            'bitmap0',
            'bitmap1',
            'bitmap2',
            'last_event',
            'link_state',
        ):
            out[k] = hex(out[k])
        out['address'] = hex(r['address'])
        return out
    return None


# Test022 processor link probe (target/peer_probe.hpp, peer_services.hpp).
PEER_MAGIC = 0x31525050
PEER_FIELDS = (
    'magic version role begin_result poll_result end_result polls safe_handoff '
    'stale_deferred start_cycles begin_ms link_ms end_ms stale_deferred_last reserved0 reserved1'
).split()
PEER_DIAGNOSTIC_FIELDS = (
    'magic version stage result requests responses last_operation '
    'last_device_result started_ms elapsed_ms link_initialized'
).split()
PEER_SNAPSHOT_FIELDS = (
    'role gpio_role driver vtable spi tx_dma rx_dma interrupt_mask spi_control '
    'spi_status spi_count tx_config rx_config driver_phase active_request queue_head '
    'queue_tail deferred_head deferred_tail gpio_table gpio_slots gpio_match0 gpio_match1 '
    'tx_pending rx_pending direction primary_state pending_request released '
    'protocol_state'
).split()
PEER_RESULTS = (
    'ok waiting choose_image admit_image ready unavailable identity role lifecycle irq '
    'bounds protocol timeout drain close'
).split()
PEER_STAGES = (
    'stopped captured opening handshake1 handshake10 serving receiving admission draining closed failed'
).split()
PEER_SNAPSHOT_WORDS = len(PEER_SNAPSHOT_FIELDS) + 6 * 5
PEER_BYTES = 4 * (len(PEER_FIELDS) + len(PEER_DIAGNOSTIC_FIELDS) + 2 * PEER_SNAPSHOT_WORDS)


def _peer_name(table, value):
    return table[value] if value < len(table) else value


def _peer_snapshot(words):
    out = dict(zip(PEER_SNAPSHOT_FIELDS, words))
    irqs = words[len(PEER_SNAPSHOT_FIELDS) :]
    out['interrupts'] = [
        dict(
            id=hex(irqs[i]),
            index=irqs[i + 1],
            handler=hex(irqs[i + 2]),
            context=hex(irqs[i + 3]),
            control=hex(irqs[i + 4]),
        )
        for i in range(0, 30, 5)
    ]
    for k in (
        'driver',
        'vtable',
        'spi',
        'tx_dma',
        'rx_dma',
        'interrupt_mask',
        'spi_control',
        'spi_status',
        'tx_config',
        'rx_config',
        'active_request',
        'queue_head',
        'queue_tail',
        'deferred_head',
        'deferred_tail',
        'gpio_table',
        'primary_state',
        'pending_request',
        'direction',
    ):
        out[k] = hex(out[k])
    return out


def peer_probe(regions, dump):
    """Test022 link probe result block, or None."""
    for r in regions:
        block = dump[r['offset'] : r['offset'] + r['bytes']]
        if r['bytes'] != PEER_BYTES or struct.unpack_from('<I', block)[0] != PEER_MAGIC:
            continue
        words = struct.unpack_from('<%dI' % (PEER_BYTES // 4), block)
        out = dict(zip(PEER_FIELDS, words))
        for k in ('begin_result', 'poll_result', 'end_result'):
            out[k] = _peer_name(PEER_RESULTS, out[k])
        for k in ('reserved0', 'reserved1'):
            del out[k]
        for k in ('magic', 'stale_deferred', 'stale_deferred_last'):
            out[k] = hex(out[k])
        out['diagnostic'] = _peer_diagnostic(words, len(PEER_FIELDS))
        out['address'] = hex(r['address'])
        return out
    return None


def _peer_diagnostic(words, at):
    d = dict(zip(PEER_DIAGNOSTIC_FIELDS, words[at : at + len(PEER_DIAGNOSTIC_FIELDS)]))
    d['magic'] = hex(d['magic'])
    d['stage'], d['result'] = _peer_name(PEER_STAGES, d['stage']), _peer_name(PEER_RESULTS, d['result'])
    d['last_device_result'] = hex(d['last_device_result'])
    at += len(PEER_DIAGNOSTIC_FIELDS)
    d['before'] = _peer_snapshot(words[at : at + PEER_SNAPSHOT_WORDS])
    d['after'] = _peer_snapshot(words[at + PEER_SNAPSHOT_WORDS : at + 2 * PEER_SNAPSHOT_WORDS])
    return d


# Test024 held processor link + menu (target/menu_link.hpp).
MENU_LINK_MAGIC = 0x314B4C4D
MENU_LINK_FIELDS = (
    'magic version role begin_result final_result end_result polls safe_handoff '
    'play link_ms release_ms end_ms keepalives echoes commands answers '
    'activity commands_seen last_command last_argument lost_ms max_gap_ms stale_deferred '
    'start_cycles'
).split()
MENU_LINK_BYTES = 4 * (len(MENU_LINK_FIELDS) + len(PEER_DIAGNOSTIC_FIELDS) + 2 * PEER_SNAPSHOT_WORDS)


def menu_link(regions, dump):
    """Test024 held link result block, or None."""
    for r in regions:
        block = dump[r['offset'] : r['offset'] + r['bytes']]
        if r['bytes'] != MENU_LINK_BYTES or struct.unpack_from('<I', block)[0] != MENU_LINK_MAGIC:
            continue
        words = struct.unpack_from('<%dI' % (MENU_LINK_BYTES // 4), block)
        out = dict(zip(MENU_LINK_FIELDS, words))
        for k in ('begin_result', 'final_result', 'end_result'):
            out[k] = _peer_name(PEER_RESULTS, out[k])
        for k in ('magic', 'last_command', 'last_argument', 'stale_deferred'):
            out[k] = hex(out[k])
        command = words[MENU_LINK_FIELDS.index('last_command')]
        out['last_command_item'] = command >> 8 & 0xFF if command >> 16 == 0x434D else None
        out['diagnostic'] = _peer_diagnostic(words, len(MENU_LINK_FIELDS))
        out['address'] = hex(r['address'])
        return out
    return None


# Test025 file boot (XY Forever): FBT1 block = 36 words, cold_storage
# Diagnostic (6 + 2 x 31 words), then the peer session Diagnostic.
FILE_BOOT_MAGIC = 0x31544246
FILE_BOOT_FIELDS = (
    'magic version role outcome storage_begin storage_end storage_safe load_reason '
    'image_bytes image_crc admit_reason read_result begin_result poll_result end_result polls '
    'committed declined copy_crc heap_brk start_cycles storage_ms hash_ms link_ms '
    'admit_ms end_ms stack_used received_bytes'
).split()
FILE_BOOT_OUTCOMES = ('none', 'file', 'no_image', 'refused', 'link', 'copy', 'role')
FILE_BOOT_LIBRARY_BYTES = 64  # LibraryInfo (library boot, version 2)
FILE_BOOT_CHOICES = ('menu', 'once', 'default')
FILE_BOOT_MENU_BYTES = 64  # xyf_boot::MenuInfo (menu boot, version 3)
FILE_BOOT_MENU_FIELDS = (
    'play catalog_bytes catalog_ok catalog_entries choice choice_entry commands commands_seen '
    'blobs catalog_ms menu_ms boot_ms last_command scan_error refused state_write'
).split()
MENU_CHOICES = ('none', 'default', 'entry')
FILE_BOOT_STAGE2_BYTES = 32  # xyf_boot::Stage2Info (--xyf-stage2, version 4)
FILE_BOOT_STAGE2_FIELDS = 'status check file_bytes version entry exit load_ms run_ms'.split()
STAGE2_STATUS = 'none ready storage open read check link copy refused'.split()
STAGE2_CHECKS = ('ok size magic abi header_crc flags window entry image_crc stage1 build_id image_sha').split()
BOOT_TIMING_MAGIC = 0x31544D42
BOOT_TIMING_FIELDS = (
    'magic version valid a_mount_start_ms a_mount_ms a_scan_ms a_read_ms a_crc_ms '
    'a_admit_ms a_sha_ms a_freeze_ms a_ready_ms b_check_ms b_receive_ms b_admit_ms b_handoff_ms'
).split()
BOOT_TIMING_BYTES = 4 * len(BOOT_TIMING_FIELDS)
BOOT_TIMING_PHASES = (
    'a_mount_ms a_scan_ms a_read_ms a_crc_ms a_admit_ms a_sha_ms a_freeze_ms '
    'b_check_ms b_receive_ms b_admit_ms b_handoff_ms'
).split()


def boot_timing_summary(block):
    if len(block) != BOOT_TIMING_BYTES:
        return None
    out = dict(zip(BOOT_TIMING_FIELDS, struct.unpack('<16I', block)))
    if out['magic'] != BOOT_TIMING_MAGIC or out['version'] != 1:
        return None
    out['magic'] = hex(out['magic'])
    for bit, name in enumerate(BOOT_TIMING_PHASES):
        if not out['valid'] & (1 << bit):
            out[name] = None
    if not out['valid'] & 1:
        out['a_mount_start_ms'] = None
    if not out['valid'] & 2:
        out['a_ready_ms'] = None
    return out


KNOB_MAGIC = 0x31424E4B
KNOB_FIELDS = (
    'magic version state frames dropped resyncs skipped moves idle read_index write_index last0 last1 '
    'knob1 knob2 knob3 knob4 pass_read max_read max_moves'
).split()
KNOB_STATES = ('off', 'on', 'identity')


def knob_summary(block):
    """The knob reader's counters (target/knob_input.hpp Result, diag.status block KNB1); None if not one."""
    if len(block) != 4 * len(KNOB_FIELDS):
        return None
    out = dict(zip(KNOB_FIELDS, struct.unpack('<13I4i3I', block)))
    if out['magic'] != KNOB_MAGIC or out['version'] != 1:
        return None
    out['magic'] = hex(out['magic'])
    out['state'] = KNOB_STATES[out['state']] if out['state'] < len(KNOB_STATES) else out['state']
    out['last'] = (struct.pack('<II', out.pop('last0'), out.pop('last1'))).hex(' ')
    return out


BOOT_DETAIL_MAGIC = 0x31445442
BOOT_DETAIL_FIELDS = (
    'magic version b_metadata_ms b_data_ms b_last_poll_ms b_policy_ms b_admit_hash_ms b_release_ms'
).split()
BOOT_DETAIL_BYTES = 4 * len(BOOT_DETAIL_FIELDS)


def boot_detail_summary(block):
    """B's transfer stages (xyf_boot::TimingDetail); 0 = not reached."""
    if len(block) != BOOT_DETAIL_BYTES:
        return None
    out = dict(zip(BOOT_DETAIL_FIELDS, struct.unpack('<8I', block)))
    if out['magic'] != BOOT_DETAIL_MAGIC or out['version'] != 1:
        return None
    out['magic'] = hex(out['magic'])
    return out


STORAGE_START_MAGIC = 0x31545353
STORAGE_START_FIELDS = 'magic version phase asks waited_ms answered_ms a_word longest_ms'.split()
STORAGE_START_PHASES = ('not asked', 'waiting for A', 'up', 'A mount failed, asking again', 'lost')
STORAGE_RESULTS = (
    'ok unavailable identity role lifecycle irq device busy activate mount write_attempt '
    'open_handles unmount shutdown write_refused write_failed verify_failed remount'
).split()
STORAGE_REPAIR = ('not needed', 'pending', 'done', 'failed')  # 192


def storage_start(regions, dump):
    """191: B's view of A's storage start (USB STATUS block SST1), with A's word decoded: the
    result of A's last read-only mount, its attempts, the media changes the mount wanted (denied)
    and how long the last attempt took."""
    for r in regions:
        block = dump[r['offset'] : r['offset'] + r['bytes']]
        if len(block) != 4 * len(STORAGE_START_FIELDS) or struct.unpack_from('<I', block)[0] != STORAGE_START_MAGIC:
            continue
        out = dict(zip(STORAGE_START_FIELDS, struct.unpack('<8I', block)))
        out['magic'] = '0x%08x' % out['magic']
        phase = out['phase']
        out['phase'] = STORAGE_START_PHASES[phase] if phase < len(STORAGE_START_PHASES) else phase
        word = out.pop('a_word')
        code = word & 0xFF
        v2 = out['version'] >= 2  # 192: denied in bits 16-21, the repair in 22-23
        out['a_mount'] = (
            None
            if not word >> 8 & 0xFF
            else dict(
                result='unreadable' if code == 0xFF else STORAGE_RESULTS[code] if code < len(STORAGE_RESULTS) else code,
                attempts=word >> 8 & 0xFF,
                denied_writes=word >> 16 & (0x3F if v2 else 0xFF),
                last_s=round((word >> 24) * 1.024, 1),
            )
        )
        if out['a_mount'] and v2:
            # The repair after an unclean shutdown (A's read-only mount failed with write_attempt): once
            # done or failed, result and last_s are the repair's (read-write mount, unmount, read-only mount).
            state = STORAGE_REPAIR[word >> 22 & 3]
            out['a_mount']['repair'] = (
                dict(state=state)
                if state in ('not needed', 'pending')
                else dict(state=state, result=out['a_mount']['result'], took_s=out['a_mount']['last_s'])
            )
        return out
    return None


def boot_timing(regions, dump):
    """Current boot's read-only timing snapshot from USB STATUS, if available."""
    for r in regions:
        out = boot_timing_summary(dump[r['offset'] : r['offset'] + r['bytes']])
        if out is not None:
            return out
    return None


STAGE2_REQUESTS = 'continue menu boot_once set_default mtp uninstall'.split()  # target/xyf_stage2.hpp Request


def stage2_exit_name(exit):
    """Last stage-2 exit: ABI 1 codes, or an ABI-2 request (code | entry << 8)."""
    if exit == 0xFFFFFFFF:
        return None
    code, entry = exit & 0xFF, exit >> 8
    if code >= len(STAGE2_REQUESTS):
        return exit
    if code in (2, 3):
        return '%s %d' % (STAGE2_REQUESTS[code], entry)
    return STAGE2_REQUESTS[code] if not entry else exit


def stage2_summary(words):
    """XY Forever stage-2 record (target/xyf_boot.hpp Stage2Info)."""
    out = dict(zip(FILE_BOOT_STAGE2_FIELDS, words))
    status = out['status']
    out['freeze_failed'] = bool(status & 0x100)
    out['status'] = _peer_name(STAGE2_STATUS, status & 0xFF)
    if out['status'] == 'refused':  # b got a's note: a's status | check << 8
        note = out['check']
        out['a_status'] = _peer_name(STAGE2_STATUS, note & 0xFF)
        out['check'] = _peer_name(STAGE2_CHECKS, note >> 8 & 0xFF)
    else:
        out['check'] = _peer_name(STAGE2_CHECKS, out['check'])
    out['entry'] = hex(out['entry'])
    out['exit'] = stage2_exit_name(out['exit'])
    return out


FILE_BOOT_REASONS = (
    'ok',
    'storage',
    'open',
    'size',
    'read',
    'policy',
    'entry',
    'report_slot',
    'census_slot',
    'heap',
    'unmount',
    'not_run',
    'no_library',
    'mismatch',
    'content',
    'content_repair',
)
STORAGE_RESULTS = (
    'ok unavailable identity role lifecycle irq device busy activate mount write_attempt '
    'open_handles unmount shutdown write_refused write_failed verify_failed remount'
).split()
STATE_WRITE_STATUS = ('none ok bad_entry unusable plan not_mounted storage hash').split()


def state_write_summary(word):
    """XY Forever set-default record: status | storage result << 8 | step << 16 | seq << 24."""
    if not word:
        return None
    status, storage, step, seq = word & 0xFF, word >> 8 & 0xFF, word >> 16 & 0xFF, word >> 24 & 0xFF
    return dict(
        status=_peer_name(STATE_WRITE_STATUS, status), storage=_peer_name(STORAGE_RESULTS, storage), step=step, seq=seq
    )


STORAGE_DIAGNOSTIC_WORDS = 6 + 2 * 31  # cold_storage::Snapshot is 31 words
FILE_BOOT_BYTES = 4 * (
    len(FILE_BOOT_FIELDS) + 8 + STORAGE_DIAGNOSTIC_WORDS + len(PEER_DIAGNOSTIC_FIELDS) + 2 * PEER_SNAPSHOT_WORDS
)


def file_boot(regions, dump):
    """Test025 file boot result block, or None."""
    for r in regions:
        block = dump[r['offset'] : r['offset'] + r['bytes']]
        menu_end = FILE_BOOT_BYTES + FILE_BOOT_LIBRARY_BYTES + FILE_BOOT_MENU_BYTES
        if (
            r['bytes']
            not in (
                FILE_BOOT_BYTES,
                FILE_BOOT_BYTES + FILE_BOOT_LIBRARY_BYTES,
                menu_end,
                menu_end + FILE_BOOT_STAGE2_BYTES,
                menu_end + BOOT_TIMING_BYTES,
                menu_end + BOOT_TIMING_BYTES + BOOT_DETAIL_BYTES,
            )
            or struct.unpack_from('<I', block)[0] != FILE_BOOT_MAGIC
        ):
            continue
        words = struct.unpack_from('<%dI' % (FILE_BOOT_BYTES // 4), block)
        out = dict(zip(FILE_BOOT_FIELDS, words))
        out['outcome'] = _peer_name(FILE_BOOT_OUTCOMES, out['outcome'])
        for k in ('load_reason', 'admit_reason'):
            out[k] = _peer_name(FILE_BOOT_REASONS, out[k])
        for k in ('storage_begin', 'storage_end'):
            out[k] = _peer_name(STORAGE_RESULTS, out[k])
        for k in ('begin_result', 'poll_result', 'end_result'):
            out[k] = _peer_name(PEER_RESULTS, out[k])
        for k in ('magic', 'image_crc', 'copy_crc', 'heap_brk'):
            out[k] = hex(out[k])
        if out['role'] == 1:
            # b has no storage or hash: since build 068 these two words time a's command answers.
            out['first_answer_ms'], out['longest_answer_ms'] = out['storage_ms'], out['hash_ms']
        at = len(FILE_BOOT_FIELDS)
        out['sha256'] = struct.pack('<8I', *words[at : at + 8]).hex()
        at += 8
        storage = words[at : at + STORAGE_DIAGNOSTIC_WORDS]
        out['storage'] = dict(
            magic=hex(storage[0]),
            version=storage[1],
            stage=storage[2],
            result=_peer_name(STORAGE_RESULTS, storage[3]),
            denied_writes=storage[4],
            handles=storage[5],
        )
        at += STORAGE_DIAGNOSTIC_WORDS
        out['diagnostic'] = _peer_diagnostic(words, at)
        if r['bytes'] >= FILE_BOOT_BYTES + FILE_BOOT_LIBRARY_BYTES:
            lib = struct.unpack_from('<8I', block, FILE_BOOT_BYTES)
            name = block[FILE_BOOT_BYTES + 32 : FILE_BOOT_BYTES + 64].split(b'\0')[0].decode('ascii', 'replace')
            out['library'] = dict(
                error=lib[0],
                choice=_peer_name(FILE_BOOT_CHOICES, lib[1]),
                entries=lib[2],
                skipped=lib[3],
                index=lib[4],
                expected_bytes=lib[5],
                expected_crc=hex(lib[6]),
                has_bytes=bool(lib[7] & 1),
                has_crc=bool(lib[7] & 2),
                matched=bool(lib[7] & 4),
                plain=bool(lib[7] & 8),
                id=name,
            )
        if r['bytes'] >= menu_end:
            menu = dict(
                zip(FILE_BOOT_MENU_FIELDS, struct.unpack_from('<16I', block, FILE_BOOT_BYTES + FILE_BOOT_LIBRARY_BYTES))
            )
            menu['choice'] = _peer_name(MENU_CHOICES, menu['choice'])
            command = menu['last_command']
            menu['last_command'] = hex(command)
            menu['last_item'] = hex(command >> 8 & 0xFF) if command >> 16 == 0x434D else None
            menu['state_write'] = state_write_summary(menu['state_write'])
            out['menu'] = menu
        if r['bytes'] == menu_end + FILE_BOOT_STAGE2_BYTES:
            out['stage2'] = stage2_summary(struct.unpack_from('<8I', block, menu_end))
        if r['bytes'] == menu_end + BOOT_TIMING_BYTES and out['version'] == 5:
            out['timing'] = boot_timing_summary(block[menu_end:])
        if r['bytes'] == menu_end + BOOT_TIMING_BYTES + BOOT_DETAIL_BYTES and out['version'] == 6:
            out['timing'] = boot_timing_summary(block[menu_end : menu_end + BOOT_TIMING_BYTES])
            out['detail'] = boot_detail_summary(block[menu_end + BOOT_TIMING_BYTES :])
        out['address'] = hex(r['address'])
        return out
    return None


# The menu's catalog as B holds it (target/xyf_catalog.hpp; B's copy at xyf_boot catalog_buffer,
# read with PEEK): None when the bytes are not a whole, CRC-checked catalog.
CATALOG_MAGIC, CATALOG_ADDRESS, CATALOG_ITEM = 0x32435958, 0x8B810000, 116


def catalog(data):
    if len(data) < 20 or struct.unpack_from('<I', data)[0] not in (0x31435958, CATALOG_MAGIC):
        return None
    count, default = struct.unpack_from('<2I', data, 4)
    size = 16 + count * CATALOG_ITEM + 4
    if count > 16 or len(data) < size or struct.unpack_from('<I', data, size - 4)[0] != zlib.crc32(data[: size - 4]):
        return None
    text = lambda b: b.split(b'\0')[0].decode('utf-8', 'replace')
    items = [
        dict(
            id=text(data[at : at + 32]),
            name=text(data[at + 32 : at + 80]),
            version=text(data[at + 80 : at + 112]),
            usable=bool(data[at + 113] & 1),
            content_missing=bool(data[at + 113] & 8),
            invalid_record=bool(data[at + 113] & 16),
        )
        for at in range(16, 16 + count * CATALOG_ITEM, CATALOG_ITEM)
    ]
    return dict(count=count, default=None if default == 0xFFFFFFFF else default, items=items)


# Test027 remote read (target/storage_server.hpp): RSV1 block, then the
# cold_storage Diagnostic of a's read-only mount.
REMOTE_READ_MAGIC = 0x31565352
REMOTE_READ_FIELDS = (
    'magic version role enabled identity_mismatch mount_begin mount_end mount_safe '
    'registered requests executed refused open_fds heap_before heap_after denied_writes '
    'reads b_window b_fd b_bytes b_close b_windows b_catalogs log_count'
).split()
REMOTE_READ_ACTIONS = (
    'none refuse_disabled refuse_class refuse_op refuse_flags refuse_path refuse_mount '
    'refuse_fd refuse_full answer_mount answer_unmount run_partition run_open run_read'
).split()
REMOTE_READ_BYTES = 4 * (len(REMOTE_READ_FIELDS) + 8 + 16 + STORAGE_DIAGNOSTIC_WORDS)
INSTALL_ACTIONS = (
    'install_none install_select install_trim install_read install_write install_refuse_partition '
    'install_refuse_selected install_refuse_op install_refuse_files'
).split()
# File engine actions (target/file_engine.hpp Action, logged from 0x60; build 155 on).
ENGINE_ACTIONS = (
    'refuse_off refuse_class refuse_op refuse_raw_layer refuse_bad_path refuse_outside_path '
    'refuse_protected_path refuse_tmp_path refuse_mount refuse_read_only refuse_handle refuse_full '
    'run_mount run_unmount run_file run_closedir run_open run_opendir run_path run_query run_read '
    'run_readdir run_write refuse_partition run_rename_from run_rename_to run_rename_over'
).split()


def _server_action(code):
    """A storage server log action: install (0x40..), file engine (0x60..) or the old read allowlist."""
    for base, names in ((0x40, INSTALL_ACTIONS), (0x60, ENGINE_ACTIONS)):
        if base <= code < base + len(names):
            return names[code - base]
    return _peer_name(REMOTE_READ_ACTIONS, code)


def remote_read(regions, dump):
    """Test027 remote read result block, or None."""
    for r in regions:
        block = dump[r['offset'] : r['offset'] + r['bytes']]
        if r['bytes'] != REMOTE_READ_BYTES or struct.unpack_from('<I', block)[0] != REMOTE_READ_MAGIC:
            continue
        words = struct.unpack_from('<%dI' % (REMOTE_READ_BYTES // 4), block)
        out = dict(zip(REMOTE_READ_FIELDS, words))
        for k in ('mount_begin', 'mount_end'):
            out[k] = _peer_name(STORAGE_RESULTS, out[k])
        for k in ('magic', 'heap_before', 'heap_after', 'identity_mismatch'):
            out[k] = hex(out[k])
        for k in ('b_fd', 'b_bytes', 'b_close'):
            out[k] = struct.unpack('<i', struct.pack('<I', out[k]))[0]
        at = len(REMOTE_READ_FIELDS)
        out['text'] = struct.pack('<8I', *words[at : at + 8]).split(b'\0')[0].decode('ascii', 'replace')
        at += 8
        out['log'] = [
            dict(op=hex(e & 0xFF), action=_server_action(e >> 8 & 0xFF), value=e >> 16)
            for e in words[at : at + min(out['log_count'], 16)]
        ]
        at += 16
        storage = words[at : at + STORAGE_DIAGNOSTIC_WORDS]
        out['storage'] = dict(
            magic=hex(storage[0]),
            version=storage[1],
            stage=storage[2],
            result=_peer_name(STORAGE_RESULTS, storage[3]),
            denied_writes=storage[4],
            handles=storage[5],
        )
        out['address'] = hex(r['address'])
        return out
    return None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('census', type=Path)
    parser.add_argument('--extract', type=Path, help='write each region as <address>.bin here')
    args = parser.parse_args()
    report, header, regions, dump = parse(args.census.read_bytes())
    if args.extract:
        args.extract.mkdir(parents=True, exist_ok=True)
        for r in regions:
            (args.extract / ('%08x.bin' % r['address'])).write_bytes(dump[r['offset'] : r['offset'] + r['bytes']])
    print(
        json.dumps(
            dict(
                header=header,
                role_byte=report['before']['role'],
                regions=[dict(address=hex(r['address']), bytes=r['bytes']) for r in regions],
                heap_result=report['heap_result'],
                boot_keys=boot_key_snapshot(regions, dump),
                key_ui=key_ui(regions, dump),
                peer_probe=peer_probe(regions, dump),
                menu_link=menu_link(regions, dump),
                file_boot=file_boot(regions, dump),
                remote_read=remote_read(regions, dump),
                usb_service=usb_service(regions, dump),
            ),
            indent=2,
        )
    )


if __name__ == '__main__':
    main()
