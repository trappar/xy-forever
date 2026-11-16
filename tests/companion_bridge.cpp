#include "../native/companions.hpp"
#include <new>
using namespace xyf_core;
using namespace xyf_core::companion;

extern "C" {
unsigned int companion_session_size() { return sizeof(CypressSession); }
void companion_session_construct(void* storage) { new (storage) CypressSession; }
int companion_encode(unsigned int command, const Byte* data, U32 size,
                     Byte* output, U32 capacity, U32* produced) {
    return static_cast<int>(cypress_encode(command, data, size, output, capacity, *produced));
}
int companion_decode(const Byte* packet, U32 size, U32 expected, const Byte** payload) {
    return static_cast<int>(cypress_decode(packet, size, expected, *payload));
}
int companion_crc(const Byte* data, U32 size, U32 initial, U32* result) {
    return static_cast<int>(crc16_ccitt(data, size, initial, *result));
}
int companion_ble_header(U32 size, const U32* version, U32 crc, U32 device,
                         Byte* output, U32 capacity, U32* produced) {
    return static_cast<int>(ble_header(size, version, crc, device, output, capacity, *produced));
}
int companion_ble_start(const U32* version, U32 kind, U32 device,
                        Byte* output, U32 capacity, U32* produced) {
    return static_cast<int>(ble_start(version, kind, device, output, capacity, *produced));
}
int companion_ble_chunk(const Byte* data, U32 size, U32 offset,
                        Byte* output, U32 capacity, U32* produced, U32* consumed) {
    return static_cast<int>(ble_chunk(data, size, offset, output, capacity, *produced, *consumed));
}
int companion_session_init(void* storage, const CypressRow* rows, U32 count, U32 mtu) {
    CypressSession* session = new (storage) CypressSession;
    return static_cast<int>(session->initialize(rows, count, mtu));
}
int companion_session_request(const void* storage, Byte* output, U32 capacity, U32* produced) {
    return static_cast<int>(static_cast<const CypressSession*>(storage)->request(output, capacity, *produced));
}
int companion_session_accept(void* storage, const Byte* packet, U32 size) {
    return static_cast<int>(static_cast<CypressSession*>(storage)->accept(packet, size));
}
int companion_session_exit(const void* storage, Byte* output, U32 capacity, U32* produced) {
    return static_cast<int>(static_cast<const CypressSession*>(storage)->exit_request(output, capacity, *produced));
}
unsigned int companion_session_phase(const void* storage) {
    return static_cast<unsigned int>(static_cast<const CypressSession*>(storage)->phase());
}
}
