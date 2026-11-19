// Host bridge for the file engine's path policy and request decision (target/file_engine.cpp).
#include "../target/file_engine.hpp"
using namespace xy_forever::file_engine;
extern "C" {
unsigned engine_decide(unsigned op, const char* path, const char* second) {
    return decide_engine(static_cast<Operation>(op), path, second);
}
unsigned engine_request(unsigned vtable, unsigned op, unsigned argument, unsigned pointer, const char* path, int on,
                        int session, int window, int fd_known, int fd_space, int dir_known, int dir_space,
                        int partition) {
    const State state = {unsigned(on), unsigned(session), unsigned(window), unsigned(fd_known), unsigned(fd_space),
                         unsigned(dir_known), unsigned(dir_space), unsigned(partition)};
    return decide_request(vtable, op, argument, pointer, path, state);
}
unsigned engine_rename_to(const char* source, const char* target, int window) {   // path 0x21 rename_to
    const State state = {1, 1, unsigned(window), 1, 1, 1, 1, 0};
    return decide_request(path_class, 0x21, rename_to, path_buffer, target, state, source);
}
unsigned engine_rename_over(const char* source, const char* target, int window) {   // path 0x21 rename_over (#4)
    const State state = {1, 1, unsigned(window), 1, 1, 1, 1, 0};
    return decide_request(path_class, 0x21, rename_over, path_buffer, target, state, source);
}
unsigned engine_rename_upload(const char* path, int window) {   // path 0x21 rename_upload (#144), as A splits it
    char target[max_path], source[max_path];
    for (unsigned i = 0; i != max_path; ++i) if (!(target[i] = path[i])) break;
    const State state = {1, 1, unsigned(window), 1, 1, 1, 1, 0};
    const bool split = split_upload(target, source);
    return decide_request(path_class, 0x21, rename_upload, path_buffer, target, state, split ? source : 0);
}
int engine_join_upload(const char* source, const char* target, char* out) { return join_upload(source, target, out); }
int engine_split_upload(char* path, char* source) { return split_upload(path, source); }
int engine_upload_size(const char* path, unsigned* size) {
    uint32_t n = 0;
    const bool ok = upload_size(path, n);
    *size = n;
    return ok;
}
void engine_upload_name(char* out, unsigned size, unsigned tag) { upload_name(out, size, tag); }
int engine_raw(unsigned vtable, unsigned op) { return raw_request(vtable, op); }
unsigned engine_max_path() { return max_path; }
unsigned engine_depth(const char* path) { return depth(path); }
}
