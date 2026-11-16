// Host bridge for the storage server's non-device part (storage_server.cpp): B's READ_FILE path
// format and the report block's size.
#include "../target/storage_server.hpp"
using namespace xy_forever::storage_server;
extern "C" {
int server_valid_path(const char* path) { return valid_path(path); }
unsigned server_result_bytes() { return sizeof(Result); }
}
