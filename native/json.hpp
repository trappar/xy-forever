#ifndef XYF_NATIVE_JSON_HPP
#define XYF_NATIVE_JSON_HPP
#include <stdint.h>
#define XYF_TINY_JSON_FREESTANDING
#include "../third_party/tiny-json/tiny-json.h"
#undef XYF_TINY_JSON_FREESTANDING
// Native JSON records. Parsing uses caller storage and never changes the source bytes:
// callers can keep hashing/checking the exact stored document (including job identities).
namespace xyf_core { namespace json {
static const uint32_t document_bytes = 8448, property_count = 128, field_count = 32,
                      list_count = 96, max_depth = 4, key_bytes = 31, value_bytes = 127;
static const char package_schema[] = "xy-forever.package";
static const char config_schema[] = "xy-forever.config";
enum Status { ok = 0, size, syntax, depth, duplicate, schema, version, type, value, capacity, unicode };
struct Workspace { char text[document_bytes + 1]; json_t properties[property_count]; };
#ifdef __bfin__
static_assert(sizeof(Workspace) == 11012, "native JSON workspace measurement changed");
#endif
struct Document { const json_t* root; Status status; };
Document parse(const char* source, uint32_t bytes, Workspace& work, const char* expected_schema);
const json_t* get(const json_t* object, const char* name);
bool same(const char* a, const char* b);
bool unsigned_value(const json_t* property, uint32_t& result);
bool bool_value(const json_t* property, bool& result);
bool text_value(const json_t* property, uint32_t maximum = value_bytes);
// The bounded text view consumed by existing native semantic readers. This is an
// in-memory adapter, never a second saved record. Lists and nulls are rendered only
// for the explicitly named schema fields; arbitrary JSON objects are not flattened.
// Validate a package payload field and measure its legacy-view value without
// rendering it. Direct semantic readers retain the same field/type/value bounds.
Status package_field_size(const json_t* field, uint32_t& bytes);
Status package_text(const Document& doc, char* out, uint32_t capacity, uint32_t& bytes);
Status config_text(const Document& doc, char* out, uint32_t capacity, uint32_t& bytes);
struct ConfigBuild {
    char base[32], generation[32];
    uint32_t set, count;
    bool expanded;
    const char* slot;
    const char* sha256;
};
struct Config {
    const char* base;
    const char* profile;
    const char* generation;
    bool expanded, unverified, safe;
    const char* mods;                  // comma-separated folder names
    const ConfigBuild* built;          // null before the first build
};
Status config_document(const Config& config, char* out, uint32_t capacity, uint32_t& bytes);
const char* message(Status status);
} }
#endif
