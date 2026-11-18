#include "xyf_library.hpp"
#include "xyf_state_write.hpp"
#include "xyf_catalog.hpp"
#include "xyf_menu.hpp"
#include "cold_storage.hpp"
#include "usb_proto.hpp"
#include <cassert>
#include <cstdio>
#include <cstring>
using namespace xyf;
using U32 = uint32_t;
using Byte = uint8_t;
namespace services = xy_forever::services;
namespace cold_storage = xy_forever::cold_storage;
namespace P = xyf::usbp;
#include "fake_fs.inc"
char slots[2][4097];
bool write_fails = false, remount_fails = false, transfer_fails = false, barrier_fails = false;
struct TestFs : FakeFs {
    bool readable = true;
    bool direct_ready() { return readable; }
    cold_storage::Result write_state_slot(const char* path, const Byte* data, services::U32 size,
                                          Byte*, services::U32& step) {
        step = 6;
        if (write_fails) return cold_storage::Result::write_failed;
        const int slot = !strcmp(path, state_write::slot_a_path) ? 0 : 1;
        memcpy(slots[slot], data, size); slots[slot][size] = 0;
        step = 9;
        if (remount_fails) { readable = false; return cold_storage::Result::remount; }
        return cold_storage::Result::ok;
    }
} storage;
bool mounted = true;
U32 a_prepared = 0, catalog_bytes = 0, storage_used = 0;
Byte scan_data[library::scratch_needed], catalog_data[catalog::max_bytes], readback[4097];
state_write::Plan test_plan;
library::Library lib;
state_write::Collector a_collector;
struct { struct { U32 scan_error, catalog_bytes; } menu; struct { U32 count, skipped; } library; } r;
struct { U32 a_state_write, last_status; } result;
U32 b_catalog_ok = 0, b_lost = 0, b_fetching = 0;   // 191: no catalog request in flight here
#define XYF_A_STORAGE_WORD 0u                          // 191: A's storage word (file_boot.cpp)
catalog::Catalog cached;
struct Stock121PeerSession {} session;
Stock121PeerSession* b_session = &session;
bool b_fetch_catalog(Stock121PeerSession&) {
    if (transfer_fails) { b_lost = 1; return false; }
    b_catalog_ok = catalog::decode(catalog_data, catalog_bytes, cached);
    return b_catalog_ok;
}
namespace xyf_boot {
const uintptr_t scan_buffer = reinterpret_cast<uintptr_t>(scan_data);
const U32 scan_capacity = sizeof(scan_data);
const uintptr_t catalog_buffer = reinterpret_cast<uintptr_t>(catalog_data);
const U32 catalog_capacity = sizeof(catalog_data);
const catalog::Catalog* catalog() { return b_catalog_ok ? &cached : nullptr; }
}
// Match the namespace spelling used by key_ui's production function.
namespace xy_forever { namespace xyf_boot { const catalog::Catalog* catalog() { return ::xyf_boot::catalog(); } } }
namespace storage_server {
const U32 usb_mode_item = 7;
bool ensure_mounted(TestFs&) { return true; }
bool held_files() { return false; }
}
void a_commit();
namespace menu_link {
bool usb_command_value(U32 command) { state_write::collect(a_collector, command); return true; }
bool usb_command(U32 item) {
    if (item == state_write::item_set_default_by_id) {
        assert(state_write::collect(a_collector, 0x434d0000u | item << 8));
        a_commit();
    }
    if (item == storage_server::usb_mode_item && barrier_fails) { b_lost = 1; return false; }
    return true;
}
void usb_patience(U32) {}
}
const U32 write_patience_ms = 60000;
U32 reply = 99, dirty = 0, app_action = 123;
menu::State app;
void send(uint8_t, uint8_t, const uint8_t* bytes, U32) { reply = P::get32(bytes); }
#include "default.inc"
void initialize() {
    storage = TestFs{};
    strcpy(slots[0], ""); strcpy(slots[1], "");
    state::Choice choice = {};
    assert(state_write::plan_default_id(choice, "stock", test_plan));
    strcpy(slots[0], test_plan.text);
    storage.files[0] = {state_write::slot_a_path, slots[0]};
    storage.files[1] = {state_write::slot_b_path, slots[1]};
    storage.files[2] = {"/yaffs2/xy-forever/firmware/stock/os.ldr", nullptr};
    storage.files[3] = {"/yaffs2/xy-forever/firmware/alt/os.ldr", nullptr};
    storage.file_count = 4;
    storage.dirs[0] = {"/yaffs2/xy-forever/firmware", {"stock", "alt"}, {false, false}, 2};
    storage.dir_count = 1;
    write_fails = remount_fails = transfer_fails = barrier_fails = false;
    mounted = true; b_lost = 0; a_prepared = 1; dirty = 0;
    a_collector = {};
    assert(library::scan_direct(storage, lib, scan_data, sizeof(scan_data)) == library::Error::ok);
    catalog_bytes = catalog::encode(lib, catalog_data, sizeof(catalog_data));
    assert(xyf_boot::refresh_catalog());
    menu::start(app, xyf_boot::catalog(), true);
}
void change(const char* id) { set_default(1, reinterpret_cast<const Byte*>(id), U32(strlen(id) + 1)); }
void expect_default(const char* id) {
    const U32 index = a_default_index(lib);
    assert(index < lib.count && !strcmp(lib.entries[index].id, id));
    assert(cached.default_index < cached.count && !strcmp(cached.items[cached.default_index].id, id));
    assert(!strcmp(menu::main_label(app, menu::item_continue), id));
}
int main() {
    initialize();
    expect_default("stock");
    change("alt");
    assert(reply == P::s_ok && a_prepared == 0xffffffffu && dirty && !app_action);
    expect_default("alt");
    change("stock");
    assert(reply == P::s_ok); expect_default("stock");

    initialize(); write_fails = true;
    change("alt");
    assert(reply == P::s_storage && a_prepared == 0xffffffffu);
    expect_default("stock");

    initialize(); remount_fails = true;
    change("alt");
    assert(reply == P::s_storage && !lib.count && !cached.count);

    initialize(); storage.fail_closedir = true;
    change("alt");
    assert(reply == P::s_storage && !lib.count && !cached.count);

    initialize(); transfer_fails = true;
    change("alt");
    assert(reply == P::s_link && !b_catalog_ok && !app.catalog);

    initialize(); barrier_fails = true;
    change("alt");
    assert(reply == P::s_link && !b_catalog_ok && !app.catalog);
    puts("default refresh checks passed");
}
