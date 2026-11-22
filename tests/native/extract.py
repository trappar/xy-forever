"""Build time (hosttest/CMakeLists.txt): the production code four C++ tests run, cut from the sources as they are.

usage: extract.py ROOT OUT. Writes into OUT:
- role_a_dispatch.inc: role A's request dispatch in Stock121PeerSession::poll (peer_dispatch_test.cpp);
- lifecycle.inc: the write window's open/close/reopen and the server's open_window/close_engine
  (v3_write_lifecycle_test.cpp; only the Blackfin memory barrier is dropped);
- fake_fs.inc, default.inc: xyf_library_test.cpp's in-memory file system, and the USB set-default command with the
  A/B cache updates it drives (v3_default_refresh_test.cpp);
- install_verify.inc: B's INSTALL read-back of the boot partition, verify and header_matches
  (sim_stock_writer_test.cpp).
"""
import sys
from pathlib import Path

ROOT, OUT = Path(sys.argv[1]), Path(sys.argv[2])
sys.path.insert(0, str(ROOT))
from xy_forever.source_text import unit_text  # noqa: E402


def function(source, signature):
    start = source.index(signature)
    return source[start:source.index('\n}\n', start) + 3]


def peer_dispatch():
    source = (ROOT / 'target/peer_services.cpp').read_text()
    start = source.index('    if(!role()){', source.index('Result Stock121PeerSession::poll(U32 now)'))
    end = source.index('    } else {\n        if(active_){', start)
    return source[start + len('    if(!role()){\n'):end]


def lifecycle():
    storage = (ROOT / 'target/cold_storage.cpp').read_text()
    server = ''.join((ROOT / 'target' / n).read_text() for n in ('storage_server.cpp', 'storage_server_engine.ipp'))
    bodies = '\n'.join(function(storage, 'Result Stock121ReadOnlySession::' + name)
                       for name in ('open_write_window(', 'close_write_window(', 'reopen_read_only('))
    bodies = bodies.replace('__asm__ volatile("ssync;" ::: "memory");', '')
    bodies = 'namespace cold_storage {\n' + bodies + '\n}\n'
    return bodies + '\n'.join(function(server, name) for name in ('bool open_window(', 'void close_engine('))


def default_refresh():
    usb = unit_text(ROOT / 'target/usb_service.cpp')
    boot = unit_text(ROOT / 'target/file_boot.cpp')
    ui = (ROOT / 'target/key_ui.cpp').read_text()
    fs = (ROOT / 'tests/native/xyf_library_test.cpp').read_text()
    fs = fs[fs.index('struct File {'):fs.index('static uint8_t scratch')]
    owner = function(usb, 'void a_set_default(').replace('cold_storage::Stock121ReadOnlySession&', 'TestFs&')
    for name, area in [('a_scan', 'scan_data'), ('a_plan', 'test_plan'), ('a_readback', 'readback')]:
        owner = owner.replace('state_write::' + name, 'reinterpret_cast<uintptr_t>(&' + area + ')')
    start = boot.index('if (xyf::state_write::collect(a_collector, command)) {')
    update = boot[boot.index('\n', start):boot.index('            session.touch(now());', start)]
    bodies = 'namespace usb_service {\n' + owner + '\n}\n'
    bodies += 'void a_commit() {\n' + update + '\n}\n'
    bodies += function(boot, 'U32 a_default_index(')
    bodies += 'namespace xyf_boot {\n' + function(boot, 'bool refresh_catalog()') + '\n}\n'
    bodies += 'namespace key_ui {\n' + function(ui, 'void refresh_catalog()') + '\n}\n'
    bodies += 'void settle_writes(bool = true) {}   // no write window in this harness\n'
    bodies += 'bool content_pending(const char*) { return false; } // content jobs have their own simulator tests\n'
    bodies += function(usb, 'const xyf::catalog::Item* catalog_item(')
    bodies += function(usb, 'U32 default_commit(U32 hash)')                  # set_default's A/B write
    bodies += function(usb, 'void set_default(uint8_t seq')
    return fs, bodies


def install_verify():
    usb = unit_text(ROOT / 'target/usb_service.cpp')
    return (function(usb, 'bool header_matches(const uint8_t* sector, uint32_t size, uint32_t crc) {') +
            function(usb, 'U32 verify(U32 size, U32 crc) {'))


OUT.mkdir(parents=True, exist_ok=True)
fake_fs, default = default_refresh()
for name, text in (('role_a_dispatch.inc', peer_dispatch()), ('lifecycle.inc', lifecycle()),
                   ('fake_fs.inc', fake_fs), ('default.inc', default), ('install_verify.inc', install_verify())):
    (OUT / name).write_text(text)
