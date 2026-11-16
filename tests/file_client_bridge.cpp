// Host bridge for B's file client (plan 2.2b; file_client.cpp non-device part): request bounds and
// the USB status of an outcome; 2.4a: change statuses, list entries, child paths; 2.4b: readdir results.
#include "../target/file_client.hpp"
using namespace xy_forever::file_client;
extern "C" {
unsigned fc_bound_ms(unsigned op, unsigned argument) { return bound_ms(op, argument); }
unsigned fc_status(unsigned outcome) { return status(outcome); }
unsigned fc_request_ms() { return request_ms; }
unsigned fc_change_ms() { return change_ms; }
unsigned fc_change_status(unsigned error) { return change_status(error); }
unsigned fc_rename_status(unsigned error) { return rename_status(error); }
unsigned fc_list_entry(const unsigned char* entry, int have, const unsigned char* record, unsigned char* out,
                       unsigned room) { return list_entry(entry, have != 0, record, out, room); }
int fc_child_path(const char* folder, const char* name, char* out) { return child_path(folder, name, out); }
unsigned fc_readdir_result(int value) { return readdir_result(value); }
}
