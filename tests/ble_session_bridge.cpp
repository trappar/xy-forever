#include "../native/ble_session.hpp"
#include <new>
using namespace xyf_core;
using namespace xyf_core::companion;
extern "C" {
U32 backplane_session_size() { return sizeof(BackplaneSession); }
void backplane_session_construct(void* p) { new (p) BackplaneSession; }
int backplane_session_initialize(void* p, const CypressRow* rows, U32 count, const Byte* key, U32 key_size) {
    return static_cast<int>(static_cast<BackplaneSession*>(p)->initialize(rows, count, key, key_size));
}
int backplane_session_request(const void* p, Byte* output, U32 capacity, U32* produced) {
    return static_cast<int>(static_cast<const BackplaneSession*>(p)->request(output, capacity, *produced));
}
int backplane_session_accept(void* p, const Byte* data, U32 size) {
    return static_cast<int>(static_cast<BackplaneSession*>(p)->accept(data, size));
}
int backplane_session_exit(const void* p, Byte* output, U32 capacity, U32* produced) {
    return static_cast<int>(static_cast<const BackplaneSession*>(p)->exit_request(output, capacity, *produced));
}
U32 backplane_session_phase(const void* p) { return static_cast<U32>(static_cast<const BackplaneSession*>(p)->phase()); }
U32 ble_session_size() { return sizeof(BleSession); }
void ble_session_construct(void* p) { new (p) BleSession; }
U32 ble_known_size() { return sizeof(BleKnownImage); }
int ble_session_initialize(void* p, const Byte* data, U32 size, const BleKnownImage* known,
                           U32 count, U32 revision) {
    return static_cast<int>(static_cast<BleSession*>(p)->initialize(data, size, known, count, revision));
}
int ble_session_abort(void* p) { return static_cast<int>(static_cast<BleSession*>(p)->abort()); }
U32 ble_session_in_session(const void* p) { return static_cast<const BleSession*>(p)->in_session() ? 1 : 0; }
U32 ble_session_valid(const void* p) { return static_cast<const BleSession*>(p)->application_valid() ? 1 : 0; }
int ble_session_version(const Byte* text, U32 size, U32* version) {
    return static_cast<int>(ble_parse_version(text, size, version));
}
int ble_session_request(const void* p, Byte* output, U32 capacity, U32* produced, BridgeRequest* info) {
    return static_cast<int>(static_cast<const BleSession*>(p)->request(output, capacity, *produced, *info));
}
int ble_session_complete(void* p, int success) {
    return static_cast<int>(static_cast<BleSession*>(p)->complete_action(success != 0));
}
int ble_session_accept(void* p, U32 command, const Byte* data, U32 size) {
    return static_cast<int>(static_cast<BleSession*>(p)->accept(command, data, size));
}
int ble_session_failed(void* p) { return static_cast<int>(static_cast<BleSession*>(p)->transport_failed()); }
U32 ble_session_phase(const void* p) { return static_cast<U32>(static_cast<const BleSession*>(p)->phase()); }
U32 ble_session_offset(const void* p) { return static_cast<const BleSession*>(p)->offset(); }
U32 ble_session_platform(const void* p) { return static_cast<const BleSession*>(p)->platform_word(); }
int ble_session_mode(const Byte* data, U32 size, U32* bootloader) {
    bool result = false;
    const Status status = ble_mode_is_bootloader(data, size, result);
    *bootloader = result ? 1 : 0;
    return static_cast<int>(status);
}
int ble_session_word(U32 revision, U32* word) {
    return static_cast<int>(ble_platform_word(revision, *word));
}
int ble_session_cypress_entry(const Byte* key, U32 size, Byte* output, U32 capacity, U32* produced) {
    return static_cast<int>(backplane_enter_packet(key, size, output, capacity, *produced));
}
}
