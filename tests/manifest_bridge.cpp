// Host bridge for the xyf_manifest tests (not linked into the device).
#include "../target/xyf_manifest.hpp"
#include "../target/xyf_store.hpp"
using namespace xyf::manifest;
namespace {
struct Collect { unsigned char* out; unsigned capacity, used, stop_at; };
// Each visited entry as: 32 digest bytes, 1 binary byte, the path, NUL.
bool collect(void* context, const FileEntry& e) {
    Collect& c = *static_cast<Collect*>(context);
    if (e.index == c.stop_at) return false;
    if (c.used + 34 + e.length > c.capacity) return false;
    for (unsigned i = 0; i != 32; ++i) c.out[c.used++] = e.sha256[i];
    c.out[c.used++] = e.binary;
    for (unsigned i = 0; i != e.length; ++i) c.out[c.used++] = static_cast<unsigned char>(e.path[i]);
    c.out[c.used++] = 0;
    return true;
}
}
extern "C" {
unsigned mf_sizes(unsigned which) {
    return which == 0 ? sizeof(Manifest) : which == 1 ? sizeof(ListReader) : which == 2 ? sizeof(Recognition)
                      : sizeof(xyf::store::Info);
}
int mf_parse(const char* text, unsigned length, Manifest* out) { return parse(text, length, *out); }
// The strict reader's legacy view and xyf_store::parse_info's fields for the same text.
int mf_legacy(const char* text, unsigned length, xyf::store::Info* strict, xyf::store::Info* legacy) {
    Manifest m;
    const int status = parse(text, length, m);
    legacy_info(m, *strict);
    xyf::store::parse_info(text, length, *legacy);
    return status;
}
// Feeds the list in pieces of `chunk` bytes; out receives the visited entries (see collect).
int mf_list(const char* data, unsigned length, unsigned chunk, unsigned stop_at, unsigned char* out,
            unsigned capacity, unsigned* used, unsigned* count, unsigned* lines) {
    ListReader r;
    list_begin(r);
    Collect c = {out, capacity, 0, stop_at};
    int status = list_ok;
    for (unsigned at = 0; at < length && status == list_ok; at += chunk)
        status = list_feed(r, data + at, length - at < chunk ? length - at : chunk, collect, &c);
    if (status == list_ok) status = list_end(r);
    *used = c.used;
    *count = r.count;
    *lines = r.lines;
    return status;
}
int mf_valid_name(const char* name) { return valid_name(name); }
void mf_file(const char* name, unsigned size, const unsigned char* prefix, unsigned length, Recognition* out) {
    recognise_file(name, size, prefix, length, *out);
}
void mf_folder(const char* name, const char* const* names, unsigned count, const Manifest* info, Recognition* out, bool vendor_pack) {
    recognise_folder(name, names, count, info, *out, vendor_pack);
}
int mf_check_os(const Manifest* dependent, const Manifest* entry) { return check_os(*dependent, *entry); }
int mf_check_mod(const Manifest* base, const Manifest* mod) { return check_mod(*base, *mod); }
int mf_check_assembled(const Manifest* base, const Manifest* assembled) { return check_assembled(*base, *assembled); }
int mf_check_apply(const Manifest* entry, const Manifest* base, const Manifest* const* mods, unsigned count,
                   unsigned* failed) {
    uint32_t f;
    const int d = check_apply(*entry, *base, mods, count, f);
    *failed = f;
    return d;
}
int mf_needs(const Manifest* dependent, const Manifest* entry) { return needs(*dependent, *entry); }
}
