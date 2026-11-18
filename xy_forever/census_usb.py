"""Decode the XY Forever USB service's result block (target/usb_service.hpp Result: magic USB1), in a census
file and in diag.status replies (re-exported by census.py). Offline only."""
import struct


def _names():
    from .census import _peer_name, state_write_summary      # census.py imports this module
    return _peer_name, state_write_summary


# XY Forever USB service (target/usb_service.hpp): USB1 block, 44 words.
USB_SERVICE_MAGIC = 0x31425355
USB_SERVICE_FIELDS = ('magic version role present identity exit configured_ms hello_at_ms frames bad_crc dropped '
                      'replies write_errors reads read_bytes last_type last_status put_size put_received put_crc '
                      'put_ok install_status install_written install_ms verify_partition verify_sectors '
                      'verify_mismatch capacity heap_before heap_after start_cycles end_ms a_identity '
                      'a_install_enabled a_selects a_trims a_reads a_writes a_refused a_selected a_last_refused '
                      'a_reboot stack_used a_state_write').split()
# Version 2 appends CDC receive counters and three read-only USB snapshots.
USB_SERVICE_V2_FIELDS = 'rx_events rx_bytes diag_count stock_callback'.split()
USB_DIAG_WORDS, USB_DIAG_SNAPSHOTS = 24, 3
USB_SERVICE_V2_WORDS = len(USB_SERVICE_FIELDS) + len(USB_SERVICE_V2_FIELDS) + USB_DIAG_WORDS * USB_DIAG_SNAPSHOTS
# Version 3 (Test 035) adds the install log counters.
USB_SERVICE_V3_FIELDS = ('log_lines log_sent log_skipped log_level_before log_sink_before install_step '
                         'install_step_ms session_cap').split()
USB_SERVICE_V3_WORDS = USB_SERVICE_V2_WORDS + len(USB_SERVICE_V3_FIELDS)
USB_SERVICE_V4_FIELDS = 'power_status boot_reason crash_restarts retired_kicks'.split()
USB_SERVICE_V4_WORDS = USB_SERVICE_V3_WORDS + len(USB_SERVICE_V4_FIELDS)
# usb_service.hpp Stage2Word / Stage2Status: the stage-2 load record in the unused diag words.
USB_STAGE2_FIELDS = 'status detail version bytes load_ms trace_code trace_value reads'.split()
USB_STAGE2_STATUS = 'none loaded missing link read check copy init hook'.split()
USB_INSTALL_STEPS = 'none checked a_install capacity writer writer_done verify_done'.split()
USB_SERVICE_EXITS = ('none not_present identity not_configured no_hello continue idle cap reboot link').split()
USB_STATUS = ('ok bad_request unknown state range crc sha storage not_found too_large image install link '
              'identity').split()


def usb_diag(words):
    """One read-only USB snapshot (target/usb_service.hpp layout)."""
    w = list(words)
    out = dict(ms=w[0], sec_4d=dict(sctl=hex(w[1]), sstat=hex(w[2])), sec_4e=dict(sctl=hex(w[3]), sstat=hex(w[4])),
               frame=w[5] & 0xffff, power=hex(w[5] >> 16 & 0xff), faddr=w[5] >> 24,
               intrtxe=hex(w[6] & 0xffff), intrrxe=hex(w[6] >> 16), intrusbe=hex(w[7] & 0xff),
               devctl=hex(w[7] >> 8 & 0xff), index=w[7] >> 16 & 0xff)
    out['endpoints'] = [dict(ep=k + 1, rxcsr=hex(w[8 + 2 * k] & 0xffff), rxcount=w[8 + 2 * k] >> 16,
                             txcsr=hex(w[9 + 2 * k] & 0xffff), rxmaxp=w[9 + 2 * k] >> 16) for k in range(3)]
    out['dma_ctl'] = [hex(w[14 + j // 2] >> (16 * (j & 1)) & 0xffff) for j in range(8)]
    out['cdc'] = dict(port=hex(w[18]), open=w[19], ring=hex(w[20]), ring_words=[hex(x) for x in w[21:24]])
    return out


# Version 5 (#107): the build identity in the first unused diag words, after the stage-2 record.
USB_IDENTITY_FIELDS = 'release image state ms kcycles'.split()
USB_IMAGE_STATES = 'none hashing known unknown'.split()


def usb_identity(words):
    """The build identity words (usb_service_identity.ipp): release version and image as hello shows them."""
    out = dict(zip(USB_IDENTITY_FIELDS, words))
    code = out.pop('release')
    out['version'] = '%d.%d.%d' % (code >> 16, code >> 8 & 0xff, code & 0xff)
    out['state'] = USB_IMAGE_STATES[out['state']] if out['state'] < len(USB_IMAGE_STATES) else out['state']
    out['image'] = '%08x' % out['image'] if out['state'] == 'known' else out['state']
    return out


def usb_service(regions, dump):
    """XY Forever USB service result block (version 1: 44 words, 2: 120, 3: 128, 4 and 5: 132), or None."""
    _peer_name, state_write_summary = _names()
    for r in regions:
        block = dump[r['offset']:r['offset'] + r['bytes']]
        if r['bytes'] not in (4 * len(USB_SERVICE_FIELDS), 4 * USB_SERVICE_V2_WORDS, 4 * USB_SERVICE_V3_WORDS,
                              4 * USB_SERVICE_V4_WORDS) or \
                struct.unpack_from('<I', block)[0] != USB_SERVICE_MAGIC:
            continue
        out = dict(zip(USB_SERVICE_FIELDS, struct.unpack_from('<%dI' % len(USB_SERVICE_FIELDS), block)))
        if r['bytes'] >= 4 * USB_SERVICE_V2_WORDS:
            at = 4 * len(USB_SERVICE_FIELDS)
            out.update(zip(USB_SERVICE_V2_FIELDS, struct.unpack_from('<4I', block, at)))
            out['stock_callback'] = hex(out['stock_callback'])
            at += 16
            out['diag'] = [usb_diag(struct.unpack_from('<%dI' % USB_DIAG_WORDS, block, at + 4 * USB_DIAG_WORDS * i))
                           for i in range(min(out['diag_count'], USB_DIAG_SNAPSHOTS))]
            if not out['diag_count']:                  # the first 8 words hold the stage-2 record
                record = dict(zip(USB_STAGE2_FIELDS, struct.unpack_from('<8I', block, at)))
                record['status'] = _peer_name(USB_STAGE2_STATUS, record['status'])
                out['stage2'] = record
                if out['version'] >= 5:
                    out['build'] = usb_identity(struct.unpack_from('<5I', block, at + 32))
        if r['bytes'] >= 4 * USB_SERVICE_V3_WORDS:
            out.update(zip(USB_SERVICE_V3_FIELDS, struct.unpack_from('<%dI' % len(USB_SERVICE_V3_FIELDS), block,
                                                                     4 * USB_SERVICE_V2_WORDS)))
            out['install_step'] = _peer_name(USB_INSTALL_STEPS, out['install_step'])
            out['log_sink_before'] = hex(out['log_sink_before'])
        if r['bytes'] == 4 * USB_SERVICE_V4_WORDS:
            out.update(zip(USB_SERVICE_V4_FIELDS, struct.unpack_from('<4I', block, 4 * USB_SERVICE_V3_WORDS)))
            out['power_status'], out['boot_reason'] = hex(out['power_status']), hex(out['boot_reason'])
        out['exit'] = _peer_name(USB_SERVICE_EXITS, out['exit'])
        for k in ('install_status', 'last_status'):
            out[k] = _peer_name(USB_STATUS, out[k])
        for k in ('magic', 'identity', 'a_identity', 'put_crc', 'heap_before', 'heap_after', 'a_last_refused'):
            out[k] = hex(out[k])
        out['a_state_write'] = state_write_summary(out['a_state_write'])
        out['address'] = hex(r['address'])
        return out
    return None
