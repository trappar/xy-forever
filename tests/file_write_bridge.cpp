// Host bridge for the XY Forever USB file write (Test 049; storage_server.cpp non-device part,
// compiled with -DXYF_USB_FILE_WRITE): b's FILE_WRITE path scope.
#include "../target/storage_server.hpp"
using namespace xy_forever::storage_server;
extern "C" {
int fw_valid_path(const char* path) { return valid_write_path(path); }
unsigned fw_item() { return file_write_item; }
unsigned fw_open_flags() { return write_open_flags; }
}
