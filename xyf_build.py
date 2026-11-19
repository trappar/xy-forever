#!/usr/bin/env python3
"""Build and verify XY Forever candidates (os.ldr) for the NAND boot partition.

Modes: --xyf-product (the product, boot model v3) and the offline-only
--xyf-stage2 and --xyf-stage2-menu builds that the product will absorb.
"""
import argparse
import json
import re
import struct
import zlib

from xy_forever.compile import SOURCE, compile_core, invoke, sha, toolchain
from xy_forever.outer import BOOT_HASH, HELPERS, ALLOWED, PROTECTED, package, verify_outer
from xy_forever.paths import STOCK_121, private_output
from xy_forever.build_formats import Firmware, elf32, header, parse_stream, require
from xy_forever.ldr import parse_ldr, validate_memory
from xy_forever.stage2_audit import STAGE2_ENTER, STAGE2_LITERALS as S2_LITERALS, STAGE2_WINDOW, \
    check_stage2_image, stage2_enter_sites
from xy_forever.probe_report import report_only_fixture
from xy_forever.otp_audit import (function_bodies, referencing_symbols, loaded_values,
                                  functions_loading, rom_references, check_otp_rules, check_otp_data)
from xy_forever import release, usb_proto, xyf_stage2 as S2

# Layout names (historical): "reporter" is the small program added to the stock OS
# image that writes the cold report to storage, "carry" the report slot it reads,
# "census" the cold memory snapshot stored below it, "module" the cold-time code.
STOCK_HASH = '60358afdaa9b58ed40411602f6d5d4e1e3e02553ff2da7e25038e71dd002b52f'
HEAP_BASE, HEAP_BYTES = 0x8c0d0380, 0x1400000
REPORTER, CARRY, END = 0x8d4ce380, 0x8d4cf380, 0x8d4d0380
HOOK = 0x8d506a02
MODULE, STACK, STACK_END = 0x8ab00000, 0x8ab0c000, 0x8ab10000
# Wide product layout (target/cold_layout.hpp; build 171): a 512 KiB code window
# 0x8ab00000..0x8ab80000, then the 16 KiB stack to 0x8ab84000. The whole reservation lies in
# FREE_GAP (check_memory_map). Only WIDE_MODES use it; every other mode keeps the narrow layout.
WIDE_STACK, WIDE_STACK_END = 0x8ab80000, 0x8ab84000
# The product build: boot model v3 (one link session per boot).
PRODUCT_MODES = ('xyf-product',)
WIDE_MODES = PRODUCT_MODES


def layout(mode):
    """(module base, code limit = stack bottom, stack top) for a build mode."""
    return (MODULE, WIDE_STACK, WIDE_STACK_END) if product_kind(mode) in WIDE_MODES else (MODULE, STACK, STACK_END)


PROBE_PROTECTED = tuple(r for r in PROTECTED if r != (0x8a000000, 0x8a810000)) + (
    (0x8a000000, 0x8a800000), (MODULE, WIDE_STACK_END), (CARRY, END))   # the inner OS never loads either layout
# Build mode -> cold report file name (# = the processor letter). A build is named by its image (the
# first 8 hex digits of its candidate's SHA-256) and carries the release version (xy_forever/release.py);
# a report says both in its contents (the USB service block), not in its name.
MODES = {
    'xyf-product': 'prod-#.bin',
    'xyf-stage2': 'stage2-#.bin',
    'xyf-stage2-menu': 'stage2m-#.bin',
    # The MTP USB test build: 188: the product (which has the composite, XYF_MTP_USB) plus the old
    # trial (XYF_MTP_USB_TRIAL: the flag file, progress marks, the result file, the 20 s no-HELLO fallback
    # restart, diag.mtp_usb op 0). Builds and verifies as the product (product_kind) apart from its name,
    # reports, hello mode and the trial.
    'xyf-mtp-usb': 'mtpu-#.bin',
    # The crash trigger test build (#102): the product plus diag.crash (XYF_CRASH_TRIGGER), a fault on purpose.
    'xyf-crash-trigger': 'crash-#.bin',
}
MANIFEST_SCHEMA = 2                 # 2: no build number; version and image. --verify refuses another schema.
# Reports go to this device folder (the reporter does not create it).
REPORT_DIR = '/yaffs2/xy-forever/log/'
# Report name written by the file image (the library os.ldr); it shows which image a processor booted.
FILE_VARIANT_REPORTS = {'xyf-product': 'pfile-#.bin', 'xyf-stage2': 'file2-#.bin',
                        'xyf-stage2-menu': 'file2m-#.bin', 'xyf-mtp-usb': 'fmtpu-#.bin',
                        'xyf-crash-trigger': 'fcrash-#.bin'}


def product_kind(mode):
    """The mode whose feature sets a build mode uses: each device test build is the product plus its test."""
    return 'xyf-product' if mode in ('xyf-mtp-usb', CRASH_TRIGGER_MODE) else mode


def hello_defines(mode):
    """The release version and mode name in the USB service's hello text (usb_proto_ids.hpp)."""
    return release.defines() + ('-DXYF_BUILD_MODE="%s"' % mode[len('xyf-'):],)


def image_name(candidate_sha256):
    """A build's name: the first 8 hex digits of its candidate's SHA-256 (hello image=, builds/<image>/)."""
    return candidate_sha256[:8]


def usb_switches(mode):
    """Build switches the USB service of a mode is compiled with that the protocol spec names."""
    mode = product_kind(mode)                       # the MTP USB test build answers what the product does
    return ({'XYF_MTP'} if features(mode)['mtp'] else set()) | \
        ({'XYF_USB_FILE_WRITE'} if mode in FILE_WRITE_MODES else set()) | \
        ({'XYF_USB_SET_DEFAULT'} if mode in USB_SET_DEFAULT_MODES else set()) | \
        ({'XYF_TFW_CHECK'} if mode in TFW_CHECK_MODES else set())


def features(mode):
    """Feature switches of a build mode (xyf-stage2-menu shares xyf-stage2's stage 1)."""
    kind = 'xyf-stage2' if mode == 'xyf-stage2-menu' else mode
    product, stage2, mtp = kind in PRODUCT_MODES, kind == 'xyf-stage2', kind == 'xyf-product'
    # stage2_service: the product's stage 2, loaded by B's USB service (ABI 3).
    return dict(stage2_menu=mode == 'xyf-stage2-menu', stage2=stage2, mtp=mtp, usb=product,
                menu_link=product, remote_read=product, file_boot=product or stage2, stage2_service=product)


# Test025 stock storage entry points allowed through cold_storage's fresh
# read-only path (the set the Test016 storage-boot module used, which also
# listed directories; file boot needs only construct/mount/open/read/close).
STORAGE_CALLS = {'889f963e', '889f9982', '889fae2e', '889fb02c', '889fb50c', '889fb5e6', '889fb6fa',
                 '889fb928', '889fbfe6', '889fbffc', '88a344a8'}
# Test025 staging/scratch reservation (outside the retained heap arena).
FILE_STAGING = (0x8b800000, 0x8c000000)
# Census (Test017): cold memory snapshot carried below the reporter (every mode).
CENSUS_BYTES = 0x110000
CENSUS_MAX = 0x400000
# Test018 stock entry points reached from the key UI (all inside KEY_UI spans).
KEY_UI_CALLS = {'88a245dc', '88a24922', '88a35e82', '88a3426a', '889eed4c', '889efa6a'}
# Test022 stock entry points reached from the peer link probe (peer_services.cpp).
# DDR targets lie inside its identity() CRC spans; the two L1 instruction targets
# (11a04f78 GPIO direction, 11a0060c request submit) cannot be read by cold data
# loads (Tests 002/005/006 stalled), so they are allowed by address only.
PEER_CALLS = {'11a04f78', '88a304c4', '88a3046c', '889f533a', '889f53ae', '889f537a', '889f52f4',
              '889f4e3e', '889f52c0', '88a30798', '889f5010', '88a319fa', '889f5db0', '889f626c',
              '889f4e82', '11a0060c', '889f65a4', '889f87d4', '889f8872', '889f4f10', '889f6508',
              '889f562e', '889f5e38'}
PEER_L1_CALLS = {'11a04f78', '11a0060c'}
# Test027 remote read: stock entries called by storage_server.cpp on a: provider registration and
# the three allowlisted executors (+ posix close for leftover handles).
SERVER_CALLS = {'889f3910', '889f76ca', '889f6fea', '889f6cd6', '889f285a'}
# a's own posix calls for the dev-menu marker check (storage_server.cpp: stat 889f3516 in
# stat_into, open 889f3b50 "wb" in restore_marker, close 889f285a there and in close_handles). On a
# they reach yaffs directly (role 0). Scoped: loaded only by storage_server::a_side stock wrappers,
# called only from those three functions (check_scoped_calls).
A_MARKER_CALLS = {'889f3516', '889f3b50', '889f285a'}
A_MARKER_CALLERS = ('9stat_into', '14restore_marker', '13close_handles')
# yaffs rename on a (889fa834 -> yaffsfs rename 889fa604), the commit step
# of #4. Changing this list needs the developer's explicit yes (docs/SAFETY.md). Allowed only through a storage_server::a_side wrapper; not
# called yet. CRC gate: the server spans.
A_RENAME_CALLS = {'889fa834'}
# B's own timed file client (file_client.cpp; changing this list needs the
# developer's explicit yes, docs/SAFETY.md) builds its requests with the five stock request builders (path 889f8d22, read
# 889f6c5c, write 889f8c10, query 889f8e36, readdir 889f88ac; the partition builder 889f87d4 is a
# peer call) and submits them on its session's slot. Builders only: none of them waits. Scoped:
# loaded only by file_client stock wrappers, called only from file_client. CRC gate: the client
# spans (= peer spans 0 and 1).
FILE_CLIENT_CALLS = {'889f8d22', '889f6c5c', '889f8c10', '889f8e36', '889f88ac'}
# The 12 stock role-1 posix client calls b used before its own file client (mount, open, read, lseek, close,
# unmount, write, mkdir; the 097 self-test's statfs, stat, opendir, readdir): each ends in
# 889f869e -> 889f7b12, which waits on a with no bound. None may be a b call again; open, close and
# stat stay for a only (A_MARKER_CALLS).
REMOVED_CLIENT_CALLS = {'889f7692', '889f3b50', '889f2924', '889f297c', '889f285a', '889f67e8', '889f28b8',
                        '889f3320', '889f366a', '889f3516', '889f41e6', '889f439a'}
# XY Forever USB service (target/usb_service.cpp): smxUSBD start/stop, CDC
# callback/available/read/write, boot partition capacity/writer/select/read-back on b,
# install executors on a, and the stock RCU reset (L1, called, never read).
USB_CALLS = {'88a35044', '88a34fec', '88a12c7c', '88a13328', '88a1334a', '88a132f6', '889f65f0', '88a06a7e',
             '889f683c', '889f6a6a', '889f6abe', '889f6b7c', '889f6dca', '11a03518'}
USB_L1_CALLS = {'11a03518'}             # the stock RCU reset
# USB stack functions only (docs/hardware/usb.md; changing this list needs the developer's
# explicit yes, docs/SAFETY.md): our MTP function driver (target/mtp_usb_stock.cpp, built only with XYF_MTP_USB,
# product and test modes): register 88a09d5c / unregister 88a09d44 the slot driver, allocate urb+buffer
# 88a092ee, submit IN 88a08f24, recycle OUT 88a08f08, free 88a09418, kick an idle endpoint 88a094aa,
# service a parked RX packet 88a0ba08. Start/stop are USB_CALLS. Reach: USB core and the stock
# heap only. The product allows these calls with spans (re/spans.py) checked at build and boot.
# The two kicks run interrupt-path code from the main loop, so they are admitted
# separately: 88a094aa (kick: finishes a sent transfer, takes the next from the queue) and 88a0ba08
# (services reception for an endpoint), MTP only, first in a test build with one device test before
# the normal build; changing this needs the developer's explicit yes (docs/SAFETY.md).
# So: first in MTP_USB_MODE (the test build; 188: the product too), compiled with XYF_MTP_USB_KICK=1; 88a0ba08 never for
# endpoint 0 (mtp_usb_stock.cpp refuses every endpoint but our bulk OUT before the call).
MTP_USB_CALLS = {'88a09d5c', '88a09d44', '88a092ee', '88a08f24', '88a08f08', '88a09418'}
MTP_USB_KICK_CALLS = {'88a094aa', '88a0ba08'}
# 188: the composite is the product. MTP_USB_CALLS and MTP_USB_KICK_CALLS (eight) plus start(0x200) (88a35044,
# already in USB_CALLS) are in the product's allowed set: USB stack functions only, any stock USB function for
# MTP, the kicks included; changing this list needs the developer's explicit yes (docs/SAFETY.md). Admitted
# only with the MTP spans verified plus the driver's own span set (target/mtp_usb_spans.hpp: TX start
# 88a0c0b0, reached via 88a08f24), both checked here against the stock image and on B at every boot before
# the start (usb_service probe_spans: a failed check starts the stock serial instead).
# The MTP USB mode is in MODES as the device test build (product_kind: the product's feature sets).
MTP_USB_MODE = 'xyf-mtp-usb'
MTP_USB_MODES = ('xyf-product', MTP_USB_MODE, 'xyf-crash-trigger')   # 188: the composite (product_kind 'xyf-product')
MTP_USB_DEFINES = ('-DXYF_MTP_USB=1', '-DXYF_MTP_USB_KICK=1')
MTP_USB_TRIAL_DEFINES = ('-DXYF_MTP_USB_TRIAL=1',)  # the test mode only
MTP_SESSION_DEFINES = ('-DXYF_MTP_SESSION=1',)
# #102: diag.crash (target/usb_service_crash_trigger.ipp, key_ui_crash.ipp) only in this test mode; never the product.
CRASH_TRIGGER_MODE, CRASH_TRIGGER_DEFINES = 'xyf-crash-trigger', ('-DXYF_CRASH_TRIGGER=1',)     # the MTP session on the composite's MTP interface
MTP_USB_TX_START_SPAN = (0x88a0c0b0, 0x80)


def mtp_usb_pid():
    """The PID in target/mtp_usb.cpp's DevInfo (0 = placeholder)."""
    return mtp_usb_ids()[1]


def mtp_usb_ids():
    """(VID, PID) in target/mtp_usb.cpp's DevInfo."""
    m = re.search(r'const DevInfo kDevInfo = \{\w+, \d+, \d+, 0, 0x([0-9a-f]{4})u, 0x([0-9a-f]{4})u,', target_source('mtp_usb.cpp'))
    require(m is not None, 'MTP USB DevInfo not found')
    return int(m.group(1), 16), int(m.group(2), 16)


# The composite's pair, the product and the test mode alike. Until 189-001: pid.codes' test pair 1209:0001
# while the shipping pair was undecided. Since then: the stock pair 2367:0021 (teenage engineering's, as the
# stock MTP and CDC drivers declare it; kept for good: #58).
MTP_USB_IDS = (0x2367, 0x0021)


def check_mtp_usb(boot, mode, targets):
    """Gate for the MTP USB driver's stock calls: the product and the test mode (kicks too), its ids, spans checked."""
    used = targets & (MTP_USB_CALLS | MTP_USB_KICK_CALLS)
    if not used:
        return set()
    require(mode in MTP_USB_MODES, 'MTP USB calls outside ' + ', '.join(MTP_USB_MODES) + ': ' + ', '.join(sorted(used)))
    require(mtp_usb_pid() != 0, 'MTP USB PID is still the 0x0000 placeholder')
    require(mtp_usb_ids() == MTP_USB_IDS, 'MTP USB build without the decided pair 2367:0021')
    own = mtp_usb_spans()
    require(any((a, n) == MTP_USB_TX_START_SPAN for a, n, _ in own), 'MTP USB spans lack the TX start span')
    spans = mtp_spans() + own
    check_spans(boot, spans, 'MTP USB', MTP_USB_CALLS | MTP_USB_KICK_CALLS, spans)
    return MTP_USB_CALLS | MTP_USB_KICK_CALLS
# Never reachable from any build: SPI-NOR erase/write, SPI metadata, the
# updater orchestrator and its bootloader/companion/spiboot handlers.
# The stock .tfw pipeline (handle_tfw 88a25d28, extract-all 88a25c6c, its
# load/run steps 88a2901a/88a28cfc/88a27d00, file decrypt 88a196cc/88a18cae, verify driver
# 88a1871c), the update-mode RAM disk 88a14820, the SPI metadata parse 11a05340 and gate setter
# 88a34ed0, the generic dispatcher 88a21456 (it reaches the metadata writer); and the two boot-ROM
# entries beside the OTP read and init that the stock bootloader does not use for keys: 0400004c
# (called only by 88a2123c, selector 0x13 with a 16-byte in/out buffer) and 04000050 (never called
# by the 1.2.1 bootloader: unknown, the likeliest place of an OTP program entry). Writing OTP is
# a brick path (SAFETY.md).
FORBIDDEN_TARGETS = {'11a062c6', '11a06376', '11a06470', '11a05312', '88a2863c', '88a2775e', '88a2830e',
                     '88a2742c', '88a2756e', '88a26f90', '88a26a80', '88a27278',
                     '88a25d28', '88a25c6c', '88a2901a', '88a28cfc', '88a27d00', '88a196cc', '88a18cae', '88a1871c',
                     '88a14820', '11a05340', '88a21456', '88a34ed0', '0400004c', '04000050',
                     # the stock role-1 RPC (submit, then spin on the done byte with no bound)
                     '889f869e', '889f7b12'}
# Boot ROM (BF707, 0x04000000..): the only entries a module may load or call:
# memcpy 040201c4, and, only with tfw.check, the OTP read 04000048 and init 04000054 of the stock
# OTP wrappers 11a05a1e / 11a05a32 (the two ROM memory-read functions; they read the device's key
# from OTP). Changing this list needs the developer's explicit yes (docs/SAFETY.md).
BOOT_ROM = (0x04000000, 0x04100000)
ROM_TARGETS = {'040201c4', '04000048', '04000054'}
OTP_CALLS = {'04000048', '04000054'}
OTP_READ, OTP_INIT, OTP_INIT_FLAG = 0x04000048, 0x04000054, 0x11b01650   # the flag: stock 11a05a32's guard byte
# tfw.check (XYF_TFW_CHECK): the only feature that reads OTP, and with it the native
# .tfw check (native/tfw_check and the decoder parts it uses) and the key provider.
TFW_CHECK_MODES = ('xyf-product',)
from xy_forever.build_modules import TFW_CHECK_MODULES, MOD_JOB_MODULES, MODS_PAGE_MODULES, GC_MODULES, USB_STORAGE_MODULES, compile_tfw
# The mod assembly job (native/mod_job and the linker, patch and content steps) runs on B.
# The menu's Mods page (target/xyf_mods*: model, view, B's reading and the job's start), product only.
# The file engine on a (storage_server.cpp; the historical MTP group): the executors
# for the query class (stat/statfs/space), readdir via the sector class and posix write via the
# write class (file_engine decide_request admits only ops 0x22/0x23/0x31, 0x2f and 0x2b there)
# and posix closedir for leftover folder handles.
MTP_CALLS = {'889f72e4', '889f6b7c', '889f6dca', '889f44f4'}
# XY Forever USB file write (Test 049, --xyf-product): b's file client, a's write
# executor 889f6dca (a USB and MTP call).
FILE_WRITE_MODES = PRODUCT_MODES
# Test 051: the library may hold plain official os.ldr images (no report slot).
PLAIN_IMAGE_MODES = PRODUCT_MODES
# Test 054: the USB service draws its own XY Forever screen (key_ui present path).
SERVICE_SCREEN_MODES = PRODUCT_MODES
# Test 058: the XY Forever menu over USB (SCREEN/KEY in the menu; its CONTINUE modes 2/3 ended with v3).
USB_MENU_MODES = PRODUCT_MODES
# Test 059: a default that can't boot opens the menu (CHECK before BOOT).
MENU_ON_FAILURE_MODES = PRODUCT_MODES
# Test 060: service only after our reboots; PLAY menu starts USB; HOLD in the menu enters the service.
SERVICE_ON_MARKER_MODES = PRODUCT_MODES
# Test 065 (boot model v3): one link session per boot; the menu is home for every USB command.
ONE_SESSION_MODES = ('xyf-product',)
# No embedded OS (project rule): the outer os.ldr is only the trampoline and
# the cold module; a boot that loads no library image shows a message and halts.
NO_EMBEDDED_MODES = ('xyf-product',)
# Stock constructor chain CRC-checked by cold_storage construct_identity().
CONSTRUCT_SPANS = ((0x889f963e, 0xc2), (0x0803e06c, 0x18), (0x88a2ec6c, 0x2bc), (0x88a2ef2c, 0x33c),
                   (0x889fb24e, 0x6c), (0x889f9ad2, 0x10))
# Direct read-only YAFFS entry points CRC-checked by cold_storage direct_identity().
DIRECT_SPANS = ((0x889fbfe6, 0x16), (0x889fb928, 0x18), (0x889fb6fa, 0x66), (0x889fb50c, 0x12),
                (0x889fb4e2, 0x2a), (0x889fb5e6, 0x22), (0x889fbffc, 0x22), (0x889f9982, 0xa))
# yaffs_write, do_write, writable mount_flags, filesystem_mount, provider_mount.
WRITE_ENTRIES = (0x889fbb0c, 0x889fb940, 0x889faf26, 0x889f9700, 0x889f75dc)
# XY Forever "set default": the only state write. yaffs_write may be referenced
# there, and only inside Stock121ReadOnlySession::write_state_slot.
STATE_WRITE_ENTRY = 0x889fbb0c
# Test 048: the product's USB SET_DEFAULT is that write (no menu SHIFT+PLAY write).
USB_SET_DEFAULT_MODES = PRODUCT_MODES
WRITE_AUDIT_MODES = USB_SET_DEFAULT_MODES
# Earlier cold_storage entry points that no build may link (directory listing, non-fresh sessions).
LEGACY_STORAGE_SYMBOLS = ('open_directory', 'next_directory', 'close_directory', 'open_file',
                          'Stock121ReadOnlySession5begin', 'storage_shutdown')
# The product's stage 2 (ABI 3, target/stage2_main.cpp): its version, and the first stage-1 release
# with the services it needs (min_stage1, a release.code; services only grow). 0x100 = 0.1.0 lies
# above every old build number, which earlier stage 1s compared it with: they refuse it.
STAGE2_SERVICE_VERSION, STAGE2_SERVICE_MIN_STAGE1 = 1, release.code('0.1.0')
GC_MODES = (*PRODUCT_MODES, 'xyf-stage2', 'xyf-stage2-menu')
# Stage 2 main (--xyf-stage2-menu, ABI 2): the stage-1 build is --xyf-stage2
# plus XYF_STAGE2_MENU; the image links the XY Forever menu modules.
STAGE2_MENU_MODULES = ('ui_gfx', 'ui_screens', 'ui_pages', 'xyf_menu', 'xyf_menu_view', 'xyf_catalog', 'xyf_store')


L1_CODE = (0x11a00000, 0x11a0c000)        # L1 instruction SRAM: callable, never read (Tests 002/005/006)
SPAN = re.compile(r'\{(0x[0-9a-f]{8})u, ?(0x[0-9a-f]+)u, ?(0x[0-9a-f]{8})u\}')


def target_source(name):
    return (SOURCE / 'target' / name).read_text()


def parse_spans(text, count, table):
    """Every {address, bytes, crc} entry of a C++ span table; count is exact, or a (minimum,) tuple."""
    spans = [(int(a, 16), int(n, 16), int(c, 16)) for a, n, c in SPAN.findall(text)]
    require(len(spans) > count[0] if isinstance(count, tuple) else len(spans) == count, table + ' span table')
    return spans


def write_spans():
    text = target_source('cold_storage.cpp')
    return parse_spans(text[text.index('bool write_identity() {'):text.index('Result Stock121ReadOnlySession::begin_fresh(')],
                       8, 'Write')


def key_ui_spans():
    return parse_spans(target_source('key_ui.cpp'), 5, 'Key UI')


def peer_spans():
    text = target_source('peer_services.cpp')
    return parse_spans(text[text.index('bool identity()'):text.index('bool logs_muted')], 9, 'Peer')


def server_spans():
    text = target_source('storage_server.cpp')
    return parse_spans(text[text.index('const U32 spans[][3]'):text.index('U32 mismatch()')], 7, 'Server')


def usb_spans():
    return parse_spans(target_source('usb_spans.hpp'), (50,), 'USB')


def mtp_spans():
    return parse_spans(target_source('mtp_spans.hpp'), (50,), 'MTP')


def mtp_usb_spans():
    """The MTP USB mode's own spans (target/mtp_usb_spans.hpp): never read by a product build."""
    return parse_spans(target_source('mtp_usb_spans.hpp'), 1, 'MTP USB')


def client_spans():
    text = target_source('file_client.cpp')
    return parse_spans(text[text.index('const U32 spans[][3]'):text.index('U32 crc(')], 2, 'File client')


def check_spans(boot, spans, table, calls=(), covered=None):
    """Every span matches the bootloader and lies outside L1 instruction memory; every call is inside a span."""
    image = Firmware(boot)
    for address, length, crc in spans:
        require(not L1_CODE[0] <= address < L1_CODE[1], table + ' span reads L1 instruction memory')
        require(zlib.crc32(image.read(address, length)) == crc,
                table + ' span CRC differs from bootloader at ' + hex(address))
    for call in sorted(calls):
        require(any(a <= int(call, 16) < a + n for a, n, _ in (spans if covered is None else covered)),
                table + ' call outside its CRC spans: ' + call)


def check_write_spans(boot):
    check_spans(boot, write_spans(), 'Write')


def check_key_ui_spans(boot):
    check_spans(boot, key_ui_spans(), 'Key UI', KEY_UI_CALLS)


def check_peer_spans(boot):
    check_spans(boot, peer_spans(), 'Peer', PEER_CALLS - PEER_L1_CALLS)


def check_server_spans(boot):
    check_spans(boot, server_spans(), 'Server', SERVER_CALLS | A_MARKER_CALLS | A_RENAME_CALLS,
                server_spans() + peer_spans())


def check_client_spans(boot):
    require(set(map(tuple, client_spans())) <= set(map(tuple, peer_spans())), 'File client spans are not peer spans')
    check_spans(boot, client_spans(), 'File client', FILE_CLIENT_CALLS)


def check_usb_spans(boot):
    check_spans(boot, usb_spans(), 'USB', USB_CALLS - USB_L1_CALLS, usb_spans() + server_spans() + peer_spans())


def check_mtp_spans(boot):
    check_spans(boot, mtp_spans(), 'MTP', MTP_CALLS | SERVER_CALLS | A_MARKER_CALLS,
                mtp_spans() + server_spans() + peer_spans())


def check_construct_spans(boot):
    image, source = Firmware(boot), target_source('cold_storage.cpp')
    for address, length in CONSTRUCT_SPANS + DIRECT_SPANS:
        require('{0x%08xu, 0x%xu, 0x%08xu}' % (address, length, zlib.crc32(image.read(address, length))) in source,
                'Constructor span CRC differs from bootloader at ' + hex(address))


def functions_referencing(listing, address):
    """Function labels whose bodies load the given address (=0x...)."""
    names, current = set(), None
    for line in listing.splitlines():
        m = re.match(r'^[0-9a-f]{8} <(.+)>:$', line)
        if m:
            current = m.group(1)
        elif '=0x%08x' % address in line and ('.l = 0x%x' % (address & 0xffff) in line.lower() or
                                              re.search(r'\b[pr]\d = 0x%x\b' % address, line.lower())):
            # Only the instruction that completes the address (objdump annotates a
            # .H load with the register's stale value from the previous code).
            names.add(current)
    return names


def check_scoped_calls(raw, addresses, wrapper, callers, what):
    """Each stock entry in addresses is loaded only by stock<> wrappers whose (mangled) name contains
    wrapper, and every function referencing such a wrapper has a name containing one of callers."""
    for address in sorted(addresses):
        users = functions_loading(raw, int(address, 16))
        for user in users:
            require(wrapper in user and 'stockILj%dE' % int(address, 16) in user,
                    '%s entry %s loaded outside its wrappers: %s' % (what, address, user))
            outside = {u for u in referencing_symbols(raw, user) if not any(c in u for c in callers)}
            require(not outside, '%s entry %s reached outside its scope: %s' % (what, address, ', '.join(sorted(outside))))


# The look-back of call_targets: 12 lines, except in the MTP responder's member functions (xyf_core::mtp::
# Responder: respond, receive, work, step set up 5-7 arguments for a FileOps callback before the call, 14-22
# lines; those calls load the pointer from memory and resolve as 'indirect').
LOOK_BACK, WIDE_LOOK_BACK, WIDE_LOOK_BACK_PREFIX = 12, 32, '__zn8xyf_core3mtp9responder'


def call_targets(listing):
    """Register-call targets in a lowercased objdump listing: hex addresses,
    'indirect' (P = [mem]) or 'unresolved'. Straight-line look-back only (no
    data flow): stock calls go through tiny noinline wrappers so the address
    load sits beside the call; anything else is unresolved and rejected."""
    targets = set()
    lines = listing.splitlines()
    function = None
    for i, line in enumerate(lines):
        named = re.match(r'^[0-9a-f]{8} <(.+)>:$', line)
        if named:
            function = named.group(1)
        via = re.search(r'(?:call|jump) \((p[0-5])\)', line)
        if not via:
            continue
        if function == STAGE2_ENTER:                    # stage 2's validated entry (see stage2_enter_sites)
            targets.add('stage2-enter')
            continue
        reg = via.group(1)
        found = None
        back = WIDE_LOOK_BACK if function and function.startswith(WIDE_LOOK_BACK_PREFIX) else LOOK_BACK
        for previous in reversed(lines[max(0, i - back):i]):
            plain = previous.split('/*')[0]
            if previous.endswith('>:') or 'rts;' in plain or ('jump' in plain and 'if ' not in plain):
                break
            fields = [f for f in plain.split('\t') if f.strip()]
            code = fields[-1] if len(fields) > 2 else ''
            if reg + ' = [' in code:
                found = 'indirect'; break
            if reg + '.l = ' in code and reg + '=0x' in previous:
                found = '%08x' % int(re.match(r'[0-9a-f]+', previous.split(reg + '=0x')[1]).group(0), 16)
                break
            if re.search(r'\b' + reg + r' = ', code):
                found = 'unresolved'; break
        targets.add(found if found else 'unresolved ' + line.split(':')[0].strip())
    return targets


SOURCE_SUFFIXES = ('.cpp', '.hpp', '.ipp', '.inc', '.s', '.py')   # .ipp: a file's included part


def own_sources():
    """Every file that can affect a build: target/, native/, the Python package, this script, the toolchain pins."""
    files = [p for d in ('target', 'native', 'xy_forever') for p in (SOURCE / d).iterdir()
             if p.is_file() and p.suffix in SOURCE_SUFFIXES]
    files += [SOURCE / 'xyf_build.py', SOURCE / 'toolchains.json']
    return sorted(str(p.relative_to(SOURCE)) for p in files)


def branch(site, destination):
    delta = destination - site
    require(delta % 2 == 0 and -(1 << 24) <= delta < (1 << 24), 'Branch range')
    units = (delta // 2) & 0xffffff
    return struct.pack('<2H', 0xe200 | (units >> 16), units & 65535)


def patches(reserve=REPORTER):
    before = struct.pack('<4H', 0xda01, HEAP_BYTES >> 16, HEAP_BYTES & 65535, 0)
    size = reserve - HEAP_BASE
    after = struct.pack('<4H', 0xda01, size >> 16, size & 65535, 0)
    return [(0x8d4d03ba, before, after), (0x8d4d03d4, before, after),
            (HOOK, bytes.fromhex('c66cbb05'), branch(HOOK, REPORTER))]


def stored_offset(stock, address, count):
    covering = [r for r in parse_stream(stock) if not r.ignored and r.length and
                address < r.address + r.length and r.address < address + count]
    require(len(covering) == 1, 'Patch is overlapped or crosses a record')
    record = covering[0]
    require(not record.fill and record.address <= address and
            address + count <= record.address + record.length, 'Patch is not stored')
    return record.offset + 16 + address - record.address


def heap_record(stock):
    overlaps = [r for r in parse_stream(stock) if not r.ignored and r.length and
                r.address < END and REPORTER < r.address + r.length]
    require(len(overlaps) == 1, 'Reservation overlaps multiple stock records')
    record = overlaps[0]
    require(record.flags == 0x104 and record.address == HEAP_BASE and
            record.length == HEAP_BYTES and record.argument == 0, 'Unexpected stock heap FILL')
    return record


# Memory map facts the layout is checked against (docs/architecture/boot.md, "Memory map").
RETAINED_ARENA = (0x88a65bf8, 0x8aa65bf4)   # stock allocator arena (table at 0x88a2a4d0, checked below)
BOOT_SOURCE = (0x8a000000, 0x8a810000)      # stock 8 MiB stream read (88a34a12) + margin; our boot copy
JOB_REGION = (0x8b000000, 0x8b800000)       # B's mod job region (cold_layout.hpp); offline modes' embedded OS
FRAMEBUFFER = (0x8c000000, 0x8c03c000)      # B's display framebuffer
# No stock code, data or allocation lies here: above the arena, below the job region; the 1.2.1
# bootloader holds no address in it (no immediate, no data word; only 0x8a000000 in 0x8a..0x8c).
FREE_GAP = (RETAINED_ARENA[1], JOB_REGION[0])


def check_memory_map():
    """Layout audit: both layouts' module + stack and the stage-2 window lie in FREE_GAP,
    clear of every buffer, and the hand-off copy cannot reach the module or its stack."""
    def apart(a, b):
        return a[1] <= b[0] or b[1] <= a[0]
    busy = (RETAINED_ARENA, BOOT_SOURCE, JOB_REGION, FILE_STAGING, FRAMEBUFFER,
            (REPORTER - CENSUS_MAX, END))
    for base, stack, stack_end in {layout(mode) for mode in MODES}:
        reserved = (base, stack_end)
        require(base == MODULE and base < stack < stack_end and stack_end - stack == 0x4000 and
                not stack_end & 3, 'Cold layout is not code then a 16 KiB stack')
        require(FREE_GAP[0] <= base and stack_end <= FREE_GAP[1], 'Cold module or stack leaves the free gap')
        require(all(apart(reserved, r) for r in busy), 'Cold module or stack overlaps a buffer')
        require(apart(reserved, STAGE2_WINDOW), 'Stage-2 window overlaps the cold module or stack')
        # Hand-off: the image is copied to the boot source (at most the staging size), and every
        # record it places is refused inside (MODULE, stack end) by cold.cpp's protected range.
        require(BOOT_SOURCE[0] + FILE_STAGING[1] - FILE_STAGING[0] <= BOOT_SOURCE[1] <= base,
                'The boot copy can reach the cold module')
    require(FREE_GAP[0] <= STAGE2_WINDOW[0] < STAGE2_WINDOW[1] <= FREE_GAP[1] and
            all(apart(STAGE2_WINDOW, r) for r in busy), 'Stage-2 window leaves the free gap')
    require((MODULE, WIDE_STACK_END) in PROBE_PROTECTED, 'Wide module + stack not protected from the OS')
    # B's mod job region, as the firmware's layout header states it, claimed by nothing else.
    header = target_source('cold_layout.hpp')
    declared = tuple(int(re.search(r'#define XYF_JOB_REGION_%s (0x[0-9a-f]{8})u' % k, header).group(1), 16)
                     for k in ('BEGIN', 'END'))
    require(declared == JOB_REGION, 'cold_layout.hpp job region differs from the memory map')
    require(all(apart(JOB_REGION, r) for r in busy if r != JOB_REGION) and apart(JOB_REGION, STAGE2_WINDOW) and
            apart(JOB_REGION, (MODULE, WIDE_STACK_END)) and apart(JOB_REGION, BOOT_SOURCE),
            'The mod job region overlaps another reservation')


def check_boot_reservations(boot):
    check_memory_map()
    mapped = Firmware(boot)
    for begin, end in ((MODULE, WIDE_STACK_END), STAGE2_WINDOW, (0x8b000000, 0x8b800000), FILE_STAGING, (CARRY, END)):
        require(all(hi <= begin or lo >= end for lo, hi, _ in mapped.segments),
                'Reservation intersects statically loaded old bootloader memory')
    require(mapped.read(0x88a2a4d0, 12) == struct.pack('<3I', 0x88a65bf8, 0x1fffffc, 0),
            'Old allocator table differs')
    require((0x88a65bf8, 0x88a65bf8 + 0x1fffffc) == RETAINED_ARENA, 'Retained arena differs from the map')
    require(0x88a65bf8 + 0x1fffffc <= MODULE and 0x88a65bf8 + 0x1fffffc < CARRY,
            'Reservation intersects retained allocator arena')


def inner_image(stock, reporter, initial_report=None, dump_bytes=0):
    require(0 < len(reporter) <= CARRY - REPORTER, 'Reporter exceeds 4 KiB')
    require(0 <= dump_bytes <= CENSUS_MAX and dump_bytes % 4 == 0, 'Census reservation')
    reserve = REPORTER - dump_bytes
    heap = heap_record(stock)
    edited = bytearray(stock)
    for address, before, after in patches(reserve):
        offset = stored_offset(stock, address, len(before))
        require(stock[offset:offset + len(before)] == before, 'Stock patch preimage differs')
        edited[offset:offset + len(after)] = after
    # Crucial: the original 20 MiB heap FILL would erase the cold report.
    # Trim it before adding the reporter; never add a FILL for the carry tail.
    edited[heap.offset:heap.offset + 16] = header(0x104, HEAP_BASE, reserve - HEAP_BASE, 0)
    at = parse_stream(stock)[-1].offset
    added = header(4, REPORTER, len(reporter), 0) + reporter
    if initial_report is not None:
        require(initial_report == report_only_fixture(), 'Unexpected report fixture')
        added += header(4, CARRY, len(initial_report), 0) + initial_report
    if dump_bytes:
        # Zero preimage for the cold census; excluded from the OS heap above.
        added += header(4, reserve, dump_bytes, 0) + bytes(dump_bytes)
    result = edited[:at] + added + edited[at:]
    result[:16] = header(0x5004, 0x11a00000, 0, len(result) - 16)
    return bytes(result)


def verify_inner(stock, inner, reporter, initial_report=None, dump_bytes=0):
    old, new = parse_stream(stock), parse_stream(inner)
    reserve = REPORTER - dump_bytes
    additions = 1 + (initial_report is not None) + bool(dump_bytes)
    require(len(new) == len(old) + additions, 'Unexpected inner record count')
    require(inner[:16] == header(0x5004, old[0].address, 0, len(inner) - 16),
            'Inner FIRST entry changed')
    at = old[-1].offset
    added = new[-1 - additions]
    require(added.offset == at and added.flags == 4 and added.address == REPORTER and
            added.length == len(reporter), 'Unexpected reporter record')
    require(inner[at + 16:at + 16 + len(reporter)] == reporter, 'Reporter bytes differ')
    added_bytes = 16 + len(reporter)
    if initial_report is not None:
        require(initial_report == report_only_fixture(), 'Unexpected report fixture')
        fixture = new[-2 - bool(dump_bytes)]
        require(fixture.flags == 4 and fixture.address == CARRY and
                fixture.length == len(initial_report) and
                inner[fixture.offset + 16:fixture.offset + 16 + fixture.length] == initial_report,
                'Report fixture record differs')
        added_bytes += 16 + len(initial_report)
    if dump_bytes:
        dump = new[-2]
        require(dump.flags == 4 and dump.address == reserve and dump.length == dump_bytes and
                not any(inner[dump.offset + 16:dump.offset + 16 + dump_bytes]), 'Census slot differs')
        added_bytes += 16 + dump_bytes
    restored = bytearray(inner[:at] + inner[at + added_bytes:])
    restored[:16] = stock[:16]
    heap = heap_record(stock)
    require(restored[heap.offset:heap.offset + 16] ==
            header(0x104, HEAP_BASE, reserve - HEAP_BASE, 0), 'Heap FILL not trimmed')
    restored[heap.offset:heap.offset + 16] = stock[heap.offset:heap.offset + 16]
    mapped = Firmware(inner)
    for address, before, after in patches(reserve):
        offset = stored_offset(stock, address, len(before))
        require(restored[offset:offset + len(after)] == after and
                mapped.read(address, len(after)) == after, 'Patch differs')
        restored[offset:offset + len(before)] = before
    require(bytes(restored) == stock, 'Unexpected changes outside diagnostic edits')
    require(mapped.read(REPORTER, len(reporter)) == reporter, 'Final reporter mapping differs')
    carry_writes = [r for r in new if not r.ignored and r.length and
                    r.address < END and CARRY < r.address + r.length]
    require(carry_writes == ([] if initial_report is None else [new[-2 - bool(dump_bytes)]]),
            'Unexpected carry write/fill')
    protected = PROBE_PROTECTED if initial_report is None else tuple(
        r for r in PROBE_PROTECTED if r != (CARRY, END))
    validate_memory(parse_ldr(inner), ALLOWED, protected)
    return dict(exact_stock_reconstruction=True, carry_range_never_written=initial_report is None,
                writer_fixture_loaded=initial_report is not None,
                heap_fill_trimmed=True, original_entry_preserved=True,
                patch_addresses=[hex(p[0]) for p in patches()])


def source_hashes():
    return {name: sha((SOURCE / name).read_bytes()) for name in own_sources()}


def descriptor_words(boot, inner):
    """The cold descriptor (target/cold.hpp): magic, version, payload size and CRC, stock helper CRCs."""
    image = Firmware(boot)
    return [0x584c4348, 1, len(inner), zlib.crc32(inner), *[zlib.crc32(image.read(a, n)) for a, n in HELPERS]]


def verify_elf(out, stem, base, limit):
    sections, symbols = elf32((out / (stem + '.elf')).read_bytes())
    module = (out / (stem + '.bin')).read_bytes()
    require(not sections.get('.bss', {}).get('size', 0), 'Uninitialized target BSS')
    initialized = [s for s in sections.values() if s['flags'] & 2 and s['type'] != 8 and s['size']]
    require(all(name in ('.text', '.rodata', '.data') or not section['flags'] & 2 or
                not section['size'] for name, section in sections.items()), 'Unexpected allocated section')
    require(initialized and min(s['address'] for s in initialized) == base, 'ELF base differs')
    require(base + len(module) <= limit, 'Module exceeds reservation')
    expected = bytearray(len(module))
    for section in initialized:
        at = section['address'] - base
        require(0 <= at and at + section['size'] <= len(module), 'ELF section outside binary')
        expected[at:at + section['size']] = section['data']
    require(bytes(expected) == module, 'ELF reconstruction differs')
    entry = 'xy_forever_cold_entry' if stem == 'module' else 'xy_forever_report_entry'
    require(symbols[entry]['value'] == base, 'ELF entry differs')
    return module, symbols


def verify(out, stock, boot):
    check_boot_reservations(boot)
    manifest = json.loads((out / 'manifest.json').read_text())
    mode = manifest.get('mode')
    require(mode in MODES, 'Unknown build mode')
    mode_name, mode = mode, product_kind(mode)        # see build()
    _, stack, stack_end = layout(mode)
    require(manifest.get('cold_stack', [hex(stack), hex(stack_end)]) == [hex(stack), hex(stack_end)],
            'Manifest stack layout differs')
    f = features(mode)
    stage2_menu, stage2, mtp, usb = f['stage2_menu'], f['stage2'], f['mtp'], f['usb']
    menu_link, remote_read, file_boot = f['menu_link'], f['remote_read'], f['file_boot']
    stage2_service = f['stage2_service']
    dump_bytes = CENSUS_BYTES
    schema = manifest.get('schema')
    require(schema == MANIFEST_SCHEMA, 'Manifest schema %s, this builder reads %d: %s' % (
        schema, MANIFEST_SCHEMA, 'update this checkout' if isinstance(schema, int) and schema > MANIFEST_SCHEMA
        else 'rebuild it'))
    require(manifest.get('version') == release.VERSION, 'Manifest release version differs')
    require(manifest.get('image') == image_name(manifest['candidate_sha256']), 'Manifest image name differs')
    require(manifest['source_sha256'] == source_hashes(), 'Source changed since build')
    compiler, _ = toolchain()
    require(manifest['tool_sha256'] == compiler['checked'], 'Tool identities changed')
    for name, digest in manifest['artifact_sha256'].items():
        require(sha((out / name).read_bytes()) == digest, 'Artifact changed: ' + name)
    module, symbols = verify_elf(out, 'module', MODULE, stack)

    def has(text):
        """Whether any linked symbol's (mangled) name contains text."""
        return any(text in name for name in symbols)

    reporter, _ = verify_elf(out, 'reporter', REPORTER, CARRY)
    result_path = (REPORT_DIR + MODES[mode_name] + '\0').encode('ascii')
    require(result_path in reporter, 'Reporter destination differs')
    # Every mode carries the cold snapshot in the admitted inner-loader DATA slot.
    require(has('xy_forever_patch_warm_snapshot') and has('trace_slot7replace'), 'Missing stream snapshot patch path')
    require(not has('xy_forever_storage_probe'), 'Stream snapshot links the direct carry path')
    at = symbols['xy_forever_trace_expected']['value'] - MODULE
    require(module[at:at + 360] == report_only_fixture(), 'Compiled trace preimage differs')
    inner = (out / 'payload.ldr').read_bytes()
    inner_checks = verify_inner(stock, inner, reporter, report_only_fixture(), dump_bytes)
    require(has('census4fill') and has('locate_zero'), 'Module lacks the census writer')
    listing = (out / 'module.asm').read_text().lower()
    state_write = mode in WRITE_AUDIT_MODES
    for address in WRITE_ENTRIES:
        if state_write and address == STATE_WRITE_ENTRY:
            continue
        require('=0x%08x' % address not in listing, 'Module references a write entry')
    if state_write:
        check_write_spans(boot)
        raw = (out / 'module.asm').read_text()
        users = functions_referencing(raw, STATE_WRITE_ENTRY)
        require(len(users) == 1 and 'y_write' in next(iter(users)),
                'yaffs_write loaded outside the y_write wrapper: ' + ', '.join(sorted(map(str, users))))
        wrapper = next(iter(users))
        callers, current = set(), None
        for line in raw.splitlines():
            m = re.match(r'^[0-9a-f]{8} <(.+)>:$', line)
            if m:
                current = m.group(1)
            elif '<%s>' % wrapper in line and current != wrapper:
                callers.add(current)
        require(len(callers) == 1 and 'write_state_slot' in next(iter(callers)),
                'y_write reached outside write_state_slot: ' + ', '.join(sorted(map(str, callers))))
    else:
        require(not has('write_state_slot') and not has('state_write'), 'State write linked outside the product builds')
    targets = call_targets(listing)
    remote_calls = SERVER_CALLS | A_MARKER_CALLS
    allowed_targets = ({'040201c4', '88a346a6', '88a3477a', 'indirect'} | KEY_UI_CALLS | PEER_CALLS | STORAGE_CALLS |
                       (remote_calls | A_RENAME_CALLS if remote_read else set()) | (USB_CALLS if usb else set()) |
                       (FILE_CLIENT_CALLS if usb else set()) |
                       (MTP_CALLS if mtp else set()) |
                       (OTP_CALLS if mode in TFW_CHECK_MODES else set()) |
                       ({'%08x' % STATE_WRITE_ENTRY} if state_write else set()) |
                       ({'stage2-enter'} if stage2 or stage2_service else set()))
    entering = stage2 or stage2_service
    require(stage2_enter_sites(listing) == (1 if entering else 0) and (STAGE2_ENTER in symbols) == entering,
            'Stage-2 trampoline must exist exactly once, and only in --xyf-stage2 and the product')
    require(not (targets & FORBIDDEN_TARGETS) and not any('=0x%s' % t in listing for t in FORBIDDEN_TARGETS),
            'Module reaches a forbidden SPI-flash/metadata/updater/companion/.tfw/OTP entry')
    rom = {t for t in targets if re.fullmatch(r'[0-9a-f]{8}', t) and BOOT_ROM[0] <= int(t, 16) < BOOT_ROM[1]}
    rom |= rom_references((out / 'module.asm').read_text())
    require(rom <= ROM_TARGETS and not rom & FORBIDDEN_TARGETS,
            'Module uses a boot-ROM entry outside 040201c4/04000048/04000054: ' + ', '.join(sorted(rom - ROM_TARGETS)))
    tfw_check = mode in TFW_CHECK_MODES
    check_otp_rules((out / 'module.asm').read_text(), symbols, tfw_check)
    check_otp_data(elf32((out / 'module.elf').read_bytes())[0], symbols)
    if tfw_check:
        require(OTP_CALLS <= targets, 'tfw.check lacks its ROM OTP calls')
        for needed in ('tfw_check5check', 'verify_header', 'otp_keys10public_key', 'otp_keys7aes_key'):
            require(has(needed), 'tfw.check lacks ' + needed)
    # Direct calls between module functions (e.g. a stored callback) are internal.
    targets = {t for t in targets if not (re.fullmatch(r'[0-9a-f]{8}', t) and MODULE <= int(t, 16) < stack)}
    allowed_targets |= check_mtp_usb(boot, mode_name, targets)
    # 188: the composite exactly in the product and the test mode, its kick calls present; the trial (the
    # 20 s fallback restart) only in the test mode.
    require(has('mtp_usb') == (mode_name in MTP_USB_MODES), 'MTP USB driver linked outside ' + ', '.join(MTP_USB_MODES))
    require(has('fallback_restart') == (mode_name == MTP_USB_MODE), 'MTP USB trial linked outside ' + MTP_USB_MODE)
    trigger = mode_name == CRASH_TRIGGER_MODE
    require(has('crash_command') == trigger and has('xyf_crash_fault') == trigger and has('crash_fault') == trigger,
            'Crash trigger linked outside ' + CRASH_TRIGGER_MODE)
    if mode_name in MTP_USB_MODES:
        require(MTP_USB_CALLS | MTP_USB_KICK_CALLS <= targets, 'MTP USB build lacks its stock calls: ' +
                ', '.join(sorted((MTP_USB_CALLS | MTP_USB_KICK_CALLS) - targets)))
        for needed in ('usb_begin', 'composite_turn', 'probe_spans', 'mtp_usb_command',
                       'mtp_usb5begin', 'mtp_usb5leave', 'serial_write'):
            require(has(needed), 'MTP USB build lacks ' + needed)
    require(targets <= allowed_targets, 'Module calls unexpected targets: ' + ', '.join(sorted(targets - allowed_targets)))
    check_key_ui_spans(boot)
    require(has('xy_forever_key_ui'), 'Key UI hook missing')
    require(KEY_UI_CALLS <= targets, 'Key UI lacks expected stock calls')
    # The stock text console (replaced by the XY Forever UI) is not an allowed target.
    require(not {'889eeab2', '889eea28'} & targets, 'XY Forever UI still calls the stock console')
    if menu_link:
        require(not has('peer_probe'), 'Menu link links the peer probe')
    else:
        require(not has('menu_link'), 'Module links the menu link')
    require(has('xyf3gfx') or has('xyf2ui'), 'XY Forever UI missing')
    require(has('xyf4menu3key'), 'XY Forever menu model missing')
    raw = (out / 'module.asm').read_text()
    # No b call of the stock posix client (their RPC waits without a bound); a keeps three.
    require(not targets & (REMOVED_CLIENT_CALLS - A_MARKER_CALLS),
            'Module calls a removed stock file-client entry: ' + ', '.join(sorted(targets & REMOVED_CLIENT_CALLS)))
    check_scoped_calls(raw, A_MARKER_CALLS - SERVER_CALLS, '14storage_server6a_side', A_MARKER_CALLERS, 'Marker check')
    check_scoped_calls(raw, {'889f285a'}, '14storage_server6a_side', A_MARKER_CALLERS, 'Leftover close')
    check_scoped_calls(raw, A_RENAME_CALLS, '14storage_server6a_side', ('14storage_server',), 'Rename')
    check_scoped_calls(raw, FILE_CLIENT_CALLS, '11file_client', ('11file_client',), 'File client')
    if usb:
        check_client_spans(boot)
        require(FILE_CLIENT_CALLS <= targets, 'File client lacks its stock builders: ' +
                ', '.join(sorted(FILE_CLIENT_CALLS - targets)))
        for needed in ('file_client9read_file', 'file_client7release', 'file_client8identity'):
            require(has(needed), 'File client lacks ' + needed)
    else:
        require(not has('file_client'), 'File client linked without the USB service')
    if remote_read:
        check_construct_spans(boot)
        check_server_spans(boot)
        require(remote_calls <= targets, 'Remote read lacks its stock calls: ' + ', '.join(sorted(remote_calls - targets)))
        for needed in ('begin_fresh', 'construct_fresh', 'storage_server7install',
                       'Stock121PeerSession5serve', 'Stock121PeerSession5reply'):
            require(has(needed), 'Remote read lacks ' + needed)
        for banned in LEGACY_STORAGE_SYMBOLS:
            require(not has(banned), 'Remote read links ' + banned)
    else:
        require(not has('storage_server'), 'Module links the storage server')
    require(has('usb_service') == usb, 'USB service linked only in the product builds')
    if usb:
        check_usb_spans(boot)
        require(USB_CALLS <= targets, 'USB service lacks its stock calls: ' + ', '.join(sorted(USB_CALLS - targets)))
        name = mode_name[len('xyf-'):]
        hello = usb_proto.hello_text(release.VERSION, None, name, usb_switches(mode)).encode('ascii')
        require(len(hello) <= usb_proto.ids.MAX_HELLO and module.count(hello + b'\0') == 1,
                'USB service hello text differs from the protocol spec')
    file_write = mode in FILE_WRITE_MODES
    require((has('engine_identity') or has('open_write_window')) == (mtp or file_write),
            'Write window linked only in the MTP / file-write modes')
    # One file engine replaced the read, file-write and MTP allowlists on a.
    for banned in ('decide_mtp', 'decide_write', 'serve_mtp', 'run_mtp', 'open_mtp', 'close_mtp', 'mtp_identity',
                   'mtp_protected_path', 'valid_mtp_path', 'mtp_boot_layer', 'serve_write', 'open_fw', 'close_fw'):
        require(not has(banned), 'Module links the replaced storage mode ' + banned)
    require(has('decide_engine') == remote_read and has('decide_request') == remote_read,
            'File engine policy linked exactly where the storage server is')
    require(not has('serve_only'), 'MTP serve-only loop linked (removed with --xyf-mtp)')
    require(has('file_client10write_file') == file_write, 'USB file write linked only in its modes')
    if file_write:
        check_mtp_spans(boot)
        check_write_spans(boot)
        require('889f6dca' in targets, 'File write lacks a\'s write executor')
        for needed in ('engine_identity', 'decide_request', 'valid_write_path', 'file_client10write_file', 'close_write_window',
                       'write_identity'):
            require(has(needed), 'File write lacks ' + needed)
    if mtp:
        check_mtp_spans(boot)
        check_write_spans(boot)
        require(MTP_CALLS <= targets, 'MTP mode lacks its stock calls: ' + ', '.join(sorted(MTP_CALLS - targets)))
        for needed in ('run_engine', 'decide_request', 'decide_engine', 'close_write_window', 'write_identity'):
            require(has(needed), 'MTP mode lacks ' + needed)
    check_peer_spans(boot)
    require({'889f4e82', '11a0060c', '889f65a4', '889f562e', '889f5e38'} <= targets,
            'Peer link lacks expected stock calls')
    if file_boot:
        check_construct_spans(boot)
        require(has('xy_forever_file_boot'), 'File boot hook missing')
        require(not has('peer_probe') and (usb or not has('menu_link')), 'File boot links another link session')
        for needed in ('begin_fresh', 'construct_fresh', 'direct_open', 'direct_read', 'Stock121PeerSession12freeze_image',
                       'Stock121PeerSession5admit', 'Stock121PeerSession5stage', 'Stock121PeerSession11freeze_blob',
                       'Stock121PeerSession8transfer', 'Stock121PeerSession6refuse', 'xyf_boot4tick',
                       'xy_forever_key_ui', 'xyf7catalog6decode', 'xyf7catalog6encode', 'scan_direct'):
            require(has(needed), 'File boot lacks ' + needed)
        for banned in LEGACY_STORAGE_SYMBOLS:
            require(not has(banned), 'File boot links ' + banned)
        require({'889f963e', '889fb928', '889fbfe6'} <= targets, 'File boot lacks its stock storage calls')
        variant, _ = verify_elf(out, 'reporter_storage', REPORTER, CARRY)
        require((REPORT_DIR + FILE_VARIANT_REPORTS[mode_name] + '\0').encode() in variant,
                'File image reporter destination differs')
        file_image = (out / 'boot.ldr').read_bytes()
        verify_inner(stock, file_image, variant, report_only_fixture(), dump_bytes)
        require(file_image != inner, 'File image equals embedded payload')
        require(len(file_image) <= FILE_STAGING[1] - FILE_STAGING[0] - 0x20000 - 512, 'File image exceeds staging')
    stage2_info = None
    if stage2:
        stage2_info = check_stage2_image(out, relocated=True)
        for needed in ('a_serve_stage2', 'b_load_stage2', 'prepare_code', 'run_stage2', 'stage26accept') + \
                (('stage26decide', 'catalog_blob') if stage2_menu else ()):
            require(has(needed), 'Stage 2 path lacks ' + needed)
        require(stage2_menu or not has('stage26decide'), 'ABI-2 request handling linked into the hello build')
        require(stage2_info['abi'] == (S2.ABI_MENU if stage2_menu else S2.ABI_HELLO) and
                stage2_info['min_stage1'] == (2 if stage2_menu else 1), 'Stage-2 image ABI differs from this stage 1')
        require(not any(lo < STAGE2_WINDOW[1] and STAGE2_WINDOW[0] < hi for lo, hi, _ in
                        Firmware((out / 'candidate.ldr').read_bytes()).segments),
                'Outer image loads into the stage-2 window')
    elif stage2_service:
        # B's USB service loads stage 2 (ABI 3); none of the offline modes' loader is linked.
        stage2_info = check_stage2_image(out)
        for needed in ('stage2_load', 'prepare_code', 'stage26accept', 's2_now', 's2_trace', 's2_reply'):
            require(has(needed), 'Stage 2 path lacks ' + needed)
        for banned in ('a_serve_stage2', 'b_load_stage2', 'run_stage2', 'stage26decide', 'stage28classify'):
            require(not has(banned), 'Product links the offline stage-2 path ' + banned)
        require(stage2_info['abi'] == S2.ABI_SERVICE and stage2_info['version'] == STAGE2_SERVICE_VERSION and
                stage2_info['min_stage1'] == STAGE2_SERVICE_MIN_STAGE1 <= release.code(),
                'Stage-2 image ABI, version or min_stage1 differs from this stage 1')
        require(not any(lo < STAGE2_WINDOW[1] and STAGE2_WINDOW[0] < hi for lo, hi, _ in
                        Firmware((out / 'candidate.ldr').read_bytes()).segments),
                'Outer image loads into the stage-2 window')
    else:
        require(not has('stage2'), 'Stage-2 loader linked outside --xyf-stage2')
    entry_asm = (out / 'module.asm').read_text().partition('xy_forever_cold_run')[0]
    require(re.search(r'\bP0\.H = 0x%x;.*\n[^\n]*\bP0\.L = 0x%x;' % (stack_end >> 16, stack_end & 0xffff),
                      entry_asm) is not None,
            'Cold entry stack top differs from the mode layout')
    embedded = b'' if mode in NO_EMBEDDED_MODES else inner
    descriptor = struct.pack('<7I', *descriptor_words(boot, embedded))
    offset = symbols['xy_forever_cold_descriptor']['value'] - MODULE
    require(module[offset:offset + len(descriptor)] == descriptor, 'Cold descriptor differs')
    candidate = (out / 'candidate.ldr').read_bytes()
    require(sha(candidate) == manifest['candidate_sha256'], 'Candidate hash differs')
    outer_checks = verify_outer(candidate, module, embedded or None, MODULE)
    return dict(passed=True, inner=inner_checks, outer=outer_checks, hardware_tested=False,
                **({'stage2': stage2_info} if stage2_info else {}),
                same_role_report=True, cold_stack_bytes=0x4000, mode=mode_name,
                report_origin='cold snapshot patched into loader stream')


def build(out, stock, boot, mode):
    require(mode in MODES, 'Unknown build mode')
    mode_name, mode = mode, product_kind(mode)        # the MTP USB test build is the product plus its probe
    mtp_usb = mode_name in MTP_USB_MODES                # 188: the product has the composite
    _, stack, stack_end = layout(mode)
    f = features(mode)
    stage2_menu, stage2, mtp, usb = f['stage2_menu'], f['stage2'], f['mtp'], f['usb']
    menu_link, remote_read, file_boot = f['menu_link'], f['remote_read'], f['file_boot']
    stage2_service = f['stage2_service']
    dump_bytes = CENSUS_BYTES
    check_boot_reservations(boot)
    check_key_ui_spans(boot)
    check_peer_spans(boot)
    require(not out.exists(), 'Use a fresh output directory')
    compiler, tools = toolchain()
    hashes = source_hashes()
    out.mkdir(parents=True)
    commands = []
    flags = compile_core(out, compiler, tools, commands, ('ldr', 'memory', 'trace_slot', 'trace_slot_zero'), split_sections=stage2)
    if mode in WIDE_MODES:
        flags = [*flags, '-DXYF_STACK_END=%#xu' % stack_end, '-DXYF_CENSUS_REGIONS=24']
    if mode in USB_SET_DEFAULT_MODES:
        flags = [*flags, '-DXYF_USB_SET_DEFAULT=1']
    if mode in FILE_WRITE_MODES:
        flags = [*flags, '-DXYF_USB_FILE_WRITE=1']
    if mode in TFW_CHECK_MODES:
        flags = [*flags, '-DXYF_TFW_CHECK=1']
    if mode in PLAIN_IMAGE_MODES:
        flags = [*flags, '-DXYF_PLAIN_IMAGES=1']
    if mode in SERVICE_SCREEN_MODES:
        flags = [*flags, '-DXYF_SERVICE_SCREEN=1']
    if mode in USB_MENU_MODES:
        flags = [*flags, '-DXYF_USB_MENU=1']
    if mode in MENU_ON_FAILURE_MODES:
        flags = [*flags, '-DXYF_MENU_ON_FAILURE=1']
    if mode in SERVICE_ON_MARKER_MODES:
        flags = [*flags, '-DXYF_SERVICE_ON_MARKER_ONLY=1', '-DXYF_REBOOT_TARGETS=1']
    if mode in ONE_SESSION_MODES:
        flags = [*flags, '-DXYF_ONE_SESSION=1']
    if mode in NO_EMBEDDED_MODES:
        flags = [*flags, '-DXYF_NO_EMBEDDED=1']
    if mode not in WIDE_MODES:
        flags = [*flags, '-DXYF_SMALL_CRC=1']        # narrow 48 KiB modules: the nibble CRC table

    def cpp(name, path, defines=()):
        sections = ['-ffunction-sections', '-fdata-sections'] if name in ('cold_storage', 'boot_services', 'retained_lookup') else []
        # The module is at its size limit: let --gc-sections drop unused
        # functions of the XY Forever modules.
        if mode in GC_MODES and name in GC_MODULES:
            sections = ['-ffunction-sections', '-fdata-sections']
        invoke([compiler['cxx'], *flags, *sections, *defines, '-I', SOURCE / 'target', '-S', path,
                '-o', out / (name + '.s')], commands)
        invoke([tools['as'], '-mcpu=bf707', '-o', out / (name + '.o'), out / (name + '.s')], commands)

    def assembly(name, path):
        invoke([tools['as'], '-mcpu=bf707', '-o', out / (name + '.o'), path], commands)

    def link(stem, base, limit, entry, names, audited=False):
        # audited (standalone stage 2): bracket the merged strings, then relink with -q (stage2_audit.py).
        literals = '%s = .; *(.rodata*.str1.*) %s = .; ' % S2_LITERALS if audited else ''
        script = out / (stem + '.ld')
        script.write_text('OUTPUT_FORMAT("elf32-bfin")\nENTRY(' + entry + ')\nSECTIONS {\n'
                          ' . = ' + hex(base) + ';\n'
                          ' .text : { KEEP(*(.text.entry)) *(.text*) }\n'
                          ' .rodata : ALIGN(4) { ' + literals + '*(.rodata*) *(.data.rel.ro*) }\n'
                          ' .data : ALIGN(4) { *(.data*) *(.sdata*) }\n'
                          ' .bss (NOLOAD) : ALIGN(4) { *(.bss*) *(.sbss*) *(COMMON) }\n'
                          ' __end = .;\n /DISCARD/ : { *(.comment*) *(.note*) *(.eh_frame*) }\n}\n'
                          'ASSERT(SIZEOF(.bss) == 0, "No implicit BSS")\n'
                          'ASSERT(__end <= ' + hex(limit) + ', "Module exceeds reservation")\n')
        gc = ['--gc-sections'] if stem == 'module' or stem == 'stage2' and (stage2_menu or stage2_service) else []
        for suffix, keep in [('', [])] + ([('.audit', ['-q'])] if audited else []):
            invoke([tools['ld'], '--no-undefined', *gc, *keep, '-T', script, '-Map', out / (stem + suffix + '.map'),
                    '-o', out / (stem + suffix + '.elf'), *[out / (n + '.o') for n in names]], commands)
        invoke([tools['objcopy'], '-O', 'binary', out / (stem + '.elf'), out / (stem + '.bin')], commands)
        (out / (stem + '.asm')).write_text(invoke([tools['objdump'], '-d', out / (stem + '.elf')], commands))

    cpp('record', SOURCE / 'target/storage_probe_record.cpp')
    census_defines = ['-DXYF_CENSUS_BASE=%#xu' % (REPORTER - CENSUS_BYTES), '-DXYF_CENSUS_BYTES=%#xu' % CENSUS_BYTES]
    report_defines = ['-DXYF_REPORT_PATH="' + REPORT_DIR + MODES[mode_name] + '"'] + census_defines
    cpp('reporter', SOURCE / 'target/storage_probe_reporter.cpp', report_defines)
    assembly('reporter_entry', SOURCE / 'target/storage_probe_reporter.s')
    link('reporter', REPORTER, CARRY, 'xy_forever_report_entry',
         ('reporter_entry', 'reporter', 'record', 'memory'))
    reporter, _ = verify_elf(out, 'reporter', REPORTER, CARRY)
    fixture = report_only_fixture()
    inner = inner_image(stock, reporter, fixture, dump_bytes)
    verify_inner(stock, inner, reporter, fixture, dump_bytes)
    (out / 'payload.ldr').write_bytes(inner)
    if file_boot:
        # The library file image: same stock OS, report and census slots as the
        # embedded payload; its reporter writes a different report name, so the
        # report names show which image each processor ran.
        cpp('reporter_storage', SOURCE / 'target/storage_probe_reporter.cpp',
            ['-DXYF_REPORT_PATH="' + REPORT_DIR + FILE_VARIANT_REPORTS[mode_name] + '"'] + census_defines)
        link('reporter_storage', REPORTER, CARRY, 'xy_forever_report_entry',
             ('reporter_entry', 'reporter_storage', 'record', 'memory'))
        variant, _ = verify_elf(out, 'reporter_storage', REPORTER, CARRY)
        file_image = inner_image(stock, variant, fixture, dump_bytes)
        verify_inner(stock, file_image, variant, fixture, dump_bytes)
        require(file_image != inner, 'File image must differ from embedded payload')
        (out / 'boot.ldr').write_bytes(file_image)
    probe_defines = (tuple(census_defines) +
                     (('-DXYF_MENU_LINK=1',) if menu_link else ()) +
                     (('-DXYF_FILE_BOOT=1',) if file_boot else ()) +
                     (('-DXYF_REMOTE_READ=1',) if remote_read else ()) +
                     (('-DXYF_USB_SERVICE=1',) if usb else ()) +
                     (('-DXYF_STAGE2=1',) if stage2 else ()) +
                     (('-DXYF_STAGE2_MENU=1',) if stage2_menu else ()) +
                     (('-DXYF_MTP=1',) if mtp else ()))
    names = ['cold_storage', 'cold_heap', 'retained_lookup', 'warm_snapshot', 'census', 'key_ui', 'knob_input']
    cpp('ui_gfx', SOURCE / 'target/ui_gfx.cpp')
    cpp('ui_screens', SOURCE / 'target/ui_screens.cpp')
    for name in ('ui_pages', 'xyf_menu', 'xyf_menu_view', 'xyf_catalog', 'xyf_store'):
        cpp(name, SOURCE / ('target/' + name + '.cpp'))   # 188: the marks come in Chrome (no define)
    names += ['boot_services']
    cpp('xyf_crc32', SOURCE / 'target/xyf_crc32.cpp')   # cold admission uses CRC in every mode
    if remote_read:
        names += ['storage_server', 'file_engine']
    if usb:
        names += ['usb_service', 'file_client']
        for name in USB_STORAGE_MODULES:
            cpp(name, SOURCE / ('target/' + name + '.cpp'))
    if stage2:
        cpp('xyf_stage2', SOURCE / 'target/xyf_stage2.cpp', ('-DXYF_STAGE2_MENU=1',) if stage2_menu else ())
        assembly('xyf_stage2_enter', SOURCE / 'target/xyf_stage2_enter.s')
    if stage2_service:
        cpp('xyf_stage2', SOURCE / 'target/xyf_stage2.cpp', ('-DXYF_STAGE2_SERVICE=1',))
        assembly('xyf_stage2_enter', SOURCE / 'target/xyf_stage2_enter.s')
    if file_boot:
        for name in ('xyf_library', 'xyf_state', 'xyf_cold_library', 'xyf_info'):
            cpp(name, SOURCE / ('target/' + name + '.cpp'))
    links = (('file_boot',) if file_boot else ()) + (('menu_link',) if menu_link else ())
    names += [*links, 'peer_services']
    cpp('peer_boot', SOURCE / 'native/peer_boot.cpp')
    tfw_check = mode in TFW_CHECK_MODES
    if tfw_check:
        compile_tfw(cpp, SOURCE)
    if mode in FILE_WRITE_MODES:
        for name in MOD_JOB_MODULES:          # -O2 as their host-proven freestanding compile: -Os calls libgcc
            cpp(name, SOURCE / ('native/' + name + '.cpp'), ('-O2',))
    mods_page = '-DXYF_ONE_SESSION=1' in flags and mode in FILE_WRITE_MODES   # target/xyf_mods_io.hpp XYF_MODS_PAGE
    if mods_page:
        for name in MODS_PAGE_MODULES:
            cpp(name, SOURCE / ('target/' + name + '.cpp'))
    cpp('hash', SOURCE / 'native/hash.cpp')
    cpp('inflate', SOURCE / 'native/inflate.cpp')   # zip::crc32_update for peer_boot
    for name in names:
        cpp(name, SOURCE / ('target/' + name + '.cpp'),
            (probe_defines if name in ('cold_storage', 'warm_snapshot', 'census', 'key_ui', 'menu_link',
                                       'file_boot', 'storage_server', 'usb_service', 'file_client') else ()) +
            (hello_defines(mode_name) + (('-DXYF_STAGE2_SERVICE=1',) if stage2_service else ()) +
             (MTP_USB_DEFINES + MTP_SESSION_DEFINES if mtp_usb else ()) +
             (MTP_USB_TRIAL_DEFINES if mode_name == MTP_USB_MODE else ()) if name == 'usb_service' else ()) +
            (CRASH_TRIGGER_DEFINES if mode_name == CRASH_TRIGGER_MODE and name in ('usb_service', 'key_ui') else ()))
    if mtp_usb:                                     # the driver, its stock binding (kicks on); 188: the product
        for name in ('mtp_usb', 'mtp_usb_stock'):
            cpp(name, SOURCE / ('target/' + name + '.cpp'), MTP_USB_DEFINES + ('-fno-zero-initialized-in-bss',))   # no BSS
        # The MTP session (target/mtp_session) over the responder (native/mtp_responder)
        cpp('mtp_session', SOURCE / 'target/mtp_session.cpp', MTP_SESSION_DEFINES + ('-fno-zero-initialized-in-bss', '-fno-function-cse'))
        cpp('mtp_responder', SOURCE / 'native/mtp_responder.cpp', ('-fno-optimize-sibling-calls', '-fno-zero-initialized-in-bss'))
    cpp('cold', SOURCE / 'target/cold.cpp', probe_defines)
    descriptor = out / 'descriptor.cpp'
    values = descriptor_words(boot, b'' if mode in NO_EMBEDDED_MODES else inner)
    descriptor.write_text('#include "cold.hpp"\nextern "C" const xy_forever::cold::Descriptor '
                          'xy_forever_cold_descriptor = {' + ','.join(hex(v) + 'u' for v in values) + '};\n')
    with descriptor.open('a') as generated:
        generated.write('#include "warm_snapshot.hpp"\nextern "C" const xyf_core::Byte '
                        'xy_forever_trace_expected[sizeof(xy_forever::storage_probe::Report)] = {' +
                        ','.join(str(byte) for byte in fixture) + '};\n')
    cpp('descriptor', descriptor)
    entry_source = (SOURCE / 'target/storage_probe_entry.s').read_text()
    require('    P0.H = 0x8ab1;\n    P0.L = 0;\n' in entry_source, 'Cold entry stack setup changed')
    entry_path = out / 'storage_probe_entry.s'      # stack top from the mode's layout (default: unchanged)
    entry_path.write_text(entry_source.replace('    P0.H = 0x8ab1;\n    P0.L = 0;\n', '    P0.H = %#x;\n    P0.L = %d;\n'
                                               % (stack_end >> 16, stack_end & 0xffff)))
    assembly('entry', entry_path)
    module_objects = (('entry', 'cold', 'descriptor', 'ldr', 'memory', 'record', 'cold_storage',
                       'cold_heap', 'retained_lookup', 'warm_snapshot', 'trace_slot', 'trace_slot_zero',
                       'census', 'key_ui', 'knob_input', 'ui_gfx', 'ui_screens',
                       'ui_pages', 'xyf_menu', 'xyf_menu_view', 'xyf_catalog', 'xyf_store', 'boot_services') +
                      (('storage_server', 'file_engine') if remote_read else ()) +
                      (('usb_service', 'file_client', 'xyf_crc32') + USB_STORAGE_MODULES if usb else ()) +
                      (('xyf_stage2', 'xyf_stage2_enter') if stage2 or stage2_service else ()) +
                      (('xyf_library', 'xyf_state', 'xyf_cold_library', 'xyf_info') if file_boot
                       else ()) +
                      ('xyf_crc32',) +
                      links + ('peer_services', 'peer_boot', 'hash', 'inflate') +
                      (('otp_keys', 'tfw_device_check') + TFW_CHECK_MODULES if tfw_check else ()) +
                      (MOD_JOB_MODULES if mode in FILE_WRITE_MODES else ()) +
                      (MODS_PAGE_MODULES if mods_page else ()) +
                      (('mtp_usb', 'mtp_usb_stock', 'mtp_session', 'mtp_responder') if mtp_usb else ()))
    module_objects = tuple(dict.fromkeys(module_objects))     # combined modes list shared objects once
    link('module', MODULE, stack, 'xy_forever_cold_entry', module_objects)
    module, _ = verify_elf(out, 'module', MODULE, stack)
    if stage2_menu:
        # Main stage 2 (ABI 2): its own freestanding copy of the XY Forever
        # menu modules, linked at the window start (unused functions dropped),
        # packed as main.xys2 for this stage-1 version.
        s2_flags = ('-DXYF_STAGE2_MENU=1', '-ffunction-sections', '-fdata-sections')
        cpp('stage2_menu', SOURCE / 'target/stage2_menu.cpp', s2_flags)
        for name in STAGE2_MENU_MODULES:
            cpp('stage2_' + name, SOURCE / ('target/' + name + '.cpp'), s2_flags)
        link('stage2', STAGE2_WINDOW[0], STAGE2_WINDOW[1], 'xyf_stage2_main',
             ('stage2_menu',) + tuple('stage2_' + n for n in STAGE2_MENU_MODULES), audited=True)
        image = (out / 'stage2.bin').read_bytes()
        (out / 'main.xys2').write_bytes(S2.pack(image, STAGE2_WINDOW[0], STAGE2_WINDOW[0], version=1,
                                               build_id='menu-034', min_stage1=2, abi=S2.ABI_MENU))
        check_stage2_image(out, relocated=True)
    elif stage2_service:
        # The product's stage 2 (ABI 3): a freestanding image at the window start, packed
        # as main.xys2 (device path /yaffs2/xy-forever/stage2/main.xys2) for this stage 1.
        cpp('stage2_main', SOURCE / 'target/stage2_main.cpp',
            ('-DXYF_STAGE2_SERVICE=1', '-ffunction-sections', '-fdata-sections'))
        link('stage2', STAGE2_WINDOW[0], STAGE2_WINDOW[1], 'xyf_stage2_main', ('stage2_main',))
        image = (out / 'stage2.bin').read_bytes()
        (out / 'main.xys2').write_bytes(S2.pack(image, STAGE2_WINDOW[0], STAGE2_WINDOW[0],
                                               version=STAGE2_SERVICE_VERSION, build_id='product-' + release.VERSION,
                                               min_stage1=STAGE2_SERVICE_MIN_STAGE1, abi=S2.ABI_SERVICE))
        check_stage2_image(out)
    elif stage2:
        # Hello stage 2: its own freestanding image (own copy of the drawing
        # layer), linked at the window start, packed as main.xys2.
        cpp('stage2_hello', SOURCE / 'target/stage2_hello.cpp')
        cpp('stage2_gfx', SOURCE / 'target/ui_gfx.cpp')
        link('stage2', STAGE2_WINDOW[0], STAGE2_WINDOW[1], 'xyf_stage2_main', ('stage2_hello', 'stage2_gfx'),
             audited=True)
        image = (out / 'stage2.bin').read_bytes()
        (out / 'main.xys2').write_bytes(S2.pack(image, STAGE2_WINDOW[0], STAGE2_WINDOW[0], version=1,
                                               build_id='hello-032'))
        check_stage2_image(out, relocated=True)
    embedded = None if mode in NO_EMBEDDED_MODES else inner
    candidate = package(module, embedded, MODULE)
    verify_outer(candidate, module, embedded, MODULE)
    (out / 'candidate.ldr').write_bytes(candidate)
    # Independently ask the authenticated assembler/linker to encode the hook.
    hook_source = out / 'hook.s'
    hook_source.write_text('.text\n.short 0\n.global probe_hook\nprobe_hook:\n JUMP.L probe_reporter;\n')
    assembly('hook', hook_source)
    invoke([tools['ld'], '-Ttext', hex(HOOK - 2), '-e', 'probe_hook', '--defsym=probe_reporter=' + hex(REPORTER),
            '-o', out / 'hook.elf', out / 'hook.o'], commands)
    invoke([tools['objcopy'], '-O', 'binary', out / 'hook.elf', out / 'hook.bin'], commands)
    require((out / 'hook.bin').read_bytes() == b'\0\0' + branch(HOOK, REPORTER) + b'\0\0',
            'Independent branch encoding differs')
    require(source_hashes() == hashes, 'Sources changed during build')
    (out / 'commands.json').write_text(json.dumps(commands, indent=2) + '\n')
    artifacts = {p.name: sha(p.read_bytes()) for p in out.iterdir() if p.is_file()}
    manifest = dict(schema=MANIFEST_SCHEMA, version=release.VERSION, image=image_name(sha(candidate)),
                    installable=False, mode=mode_name,
                    report_origin='cold snapshot patched into loader stream',
                    hardware_tested=False, candidate_sha256=sha(candidate), candidate_bytes=len(candidate),
                    module_bytes=len(module), reporter_bytes=len(reporter), stock_sha256=sha(stock),
                    bootloader_reference_sha256=sha(boot), source_sha256=hashes,
                    tool_sha256=compiler['checked'], artifact_sha256=artifacts,
                    toolchain_root=str(compiler['root']), compiler_flags=flags,
                    native_linked_objects=module_objects,
                    reserved=[hex(REPORTER), hex(END)],
                    carry=[hex(CARRY), hex(END)], cold_stack=[hex(stack), hex(stack_end)],
                    limits=['Not hardware-qualified by building: install over USB only after --verify and the test suite',
                            'Mutates one admitted DATA slot after checking the original whole-image CRC',
                            'No companion or SPI programming'])
    (out / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    result = verify(out, stock, boot)
    (out / 'verification.json').write_text(json.dumps(result, indent=2) + '\n')
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--out', required=True, type=private_output)
    parser.add_argument('--stock', type=private_output, default=STOCK_121 / 'os.ldr')
    parser.add_argument('--bootloader', type=private_output, default=STOCK_121 / 'bootloader.ldr')
    parser.add_argument('--verify', action='store_true', help='Verify an existing build directory (mode from its manifest)')
    modes = parser.add_mutually_exclusive_group()
    parser.set_defaults(mode=None)
    modes.add_argument('--xyf-product', dest='mode', action='store_const', const='xyf-product',
                       help='The XY Forever product: USB service, menu, file/library boot (wide layout)')
    modes.add_argument('--xyf-mtp-usb', dest='mode', action='store_const', const='xyf-mtp-usb',
                       help='Device test: the product plus the MTP USB probe (diag.mtp_usb); never the product')
    modes.add_argument('--xyf-crash-trigger', dest='mode', action='store_const', const=CRASH_TRIGGER_MODE,
                       help='Device test (#102): the product plus diag.crash, a fault on purpose; never the product')
    modes.add_argument('--xyf-stage2', dest='mode', action='store_const', const='xyf-stage2',
                       help='Offline (Test032): menu boot + read-only "hello" stage 2 (PLAY: load, check, run, return)')
    modes.add_argument('--xyf-stage2-menu', dest='mode', action='store_const', const='xyf-stage2-menu',
                       help='Offline (Test034): --xyf-stage2 stage 1 with ABI 2; main.xys2 runs the XY Forever menu')
    args = parser.parse_args()
    require(args.verify or args.mode, 'Choose a build mode (normally --xyf-product)')
    stock, boot = args.stock.read_bytes(), args.bootloader.read_bytes()
    require(sha(stock) == STOCK_HASH and sha(boot) == BOOT_HASH, 'Exact stock 1.2.1 inputs required')
    result = verify(args.out, stock, boot) if args.verify else build(args.out, stock, boot, args.mode)
    print(json.dumps({k: v for k, v in result.items() if k in
                      ('passed', 'candidate_sha256', 'candidate_bytes', 'module_bytes', 'reporter_bytes', 'stage2')},
                     indent=2))


if __name__ == '__main__':
    main()
