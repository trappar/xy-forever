#ifndef XYF_MANIFEST_HPP
#define XYF_MANIFEST_HPP
#include <stdint.h>
// The package model's manifests (docs/decisions/010-package-model.md;
// docs/STORAGE.md): the strict info.txt reader, the streamed files.sha256 reader, the
// recogniser for dropped files and folders, and the device's exact-match dependency rules.
// Freestanding like xyf_store: no libc, no allocation, no struct copies, every loop bounded by
// its input. Refuse, never guess: every input the readers cannot take within their bounds gets
// a specific status. The library scan still reads info.txt with xyf_store::parse_info (lenient,
// unchanged); legacy_info() gives the same fields from a manifest this reader accepted.
namespace xyf { namespace store { struct Info; } }
namespace xyf { namespace manifest {
// info.txt bounds. Text values hold at most capacity - 1 bytes.
static const uint32_t max_text = 4096, max_line = 255, max_keys = 32, max_key = 31, max_value = 127;

enum Type : uint8_t {
    type_none = 0,         // no type= key: a firmware entry (every info.txt written before type= existed)
    type_firmware, type_mod_base, type_mod,
    type_companion         // parsed only to be refused (Status refused_type)
};
// Known keys, as bit numbers of Manifest::keys (and Manifest::key on an error).
enum Key : uint8_t {
    key_type, key_name, key_version, key_kind, key_bytes, key_crc32, key_sha256, key_content,
    key_needs_os, key_generation, key_profile, key_machine, key_source, key_licence, key_machines, key_count,
    key_unknown = 0xff
};
enum Machine : uint8_t { machine_none, machine_synth, machine_drum, machine_sample, machine_effect,
                         machine_player, machine_extension };
enum Status : uint8_t {
    ok = 0,
    too_large,             // more than max_text bytes
    empty,                 // no key line at all (zero bytes, or only blanks and comments)
    no_newline,            // the last line has no LF (a cut copy looks like this)
    line_too_long,         // more than max_line bytes before the line ending
    bad_byte,              // a byte outside 0x20..0x7e (tab: only around key, '=' and value; CR: only before LF)
    syntax,                // a line that is not blank, a comment or key=value
    bad_key,               // key not [a-z0-9_]{1,31}
    duplicate_key,         // any key (known or not) given twice
    too_many_keys,         // more than max_keys key lines
    value_too_long,        // longer than the key allows (unknown keys: max_value)
    empty_value,           // a known key with nothing after '='
    bad_number,            // bytes: not 1-10 digits without a leading zero, or above 2^32-1
    bad_hex,               // crc32: not 8, sha256/needs_os: not 64 lowercase hex digits
    bad_value,             // kind/machine not a known word; generation/profile not a token
    unknown_type,          // type= not firmware, mod-base, mod or companion
    refused_type,          // type=companion (docs/SAFETY.md: the device never handles companions)
    key_not_for_type,      // a known key this type does not take
    missing_key            // a key this type requires (or generation/needs_os without the other)
};

struct Manifest {
    uint8_t status, type, kind, machine;    // kind: store::Kind numbering (0 absent)
    uint8_t key;                            // the key an error concerns, or key_unknown
    uint8_t reserved;
    uint16_t line;                          // 1-based line of the error, 0 if none
    uint16_t keys;                          // 1 << Key for each known key present
    uint16_t lines;                         // lines read
    uint32_t bytes, crc32, machines;
    uint8_t sha256[32], needs_os[32];
    char name[48], version[32], content[32], generation[32], profile[32];
    char source[128], licence[64];
};
// On ok every present key's value is stored; on an error only status, line, key, lines and
// (when it was read) type are set and every other field is zero.
Status parse(const char* text, uint32_t length, Manifest& out);
bool has(const Manifest& m, Key key);
// The fields xyf_store::parse_info gives for the same text (only for a manifest that parsed ok).
void legacy_info(const Manifest& m, store::Info& info);

// files.sha256: "shasum -a 256" lines, "<64 lowercase hex><space><space or *><path>\n",
// strictly ascending by path (bytewise), so a duplicate is the line just before (bounded memory).
static const uint32_t max_path = 160, max_files = 4096, list_line = 64 + 2 + max_path;
enum ListStatus : uint8_t {
    list_ok = 0,
    list_empty,            // no entry at all
    list_no_newline,       // the last line has no LF
    list_line_too_long,    // more than list_line bytes before LF
    list_bad_hex,          // not 64 lowercase hex digits at the start
    list_syntax,           // not two spaces or space + '*' after the hex
    list_bad_path,         // empty, absolute, ".", "..", a name ending in '.' or ' ', a byte
                           // outside 0x20..0x7e, '\\' or ':', or longer than max_path
    list_refused_path,     // names a bootloader member, or the list itself
    list_duplicate,        // the same path as the line before
    list_order,            // sorts before the line before
    list_too_many,         // more than max_files entries
    list_stopped           // the visitor returned false
};
struct FileEntry {
    uint8_t sha256[32];
    const char* path;      // NUL-terminated, valid only during the visit
    uint32_t length, index;
    bool binary;           // '*' marker (shasum -b); means nothing on the device
};
typedef bool (*FileVisit)(void* context, const FileEntry& entry);
struct ListReader {
    char line[list_line + 1], previous[max_path + 1];
    uint32_t length, previous_length, lines, count;
    uint8_t status;
};
void list_begin(ListReader& reader);
// Feed the file in pieces of any size; visit is called once per accepted line, in order.
// After an error every call returns it again (line: reader.lines).
ListStatus list_feed(ListReader& reader, const char* data, uint32_t length, FileVisit visit, void* context);
// After the last piece: refuses an unterminated last line and an empty list.
ListStatus list_end(ListReader& reader);

// The recogniser: what a dropped file or folder is. It reads only the bytes it is handed.
enum Kind : uint8_t {
    kind_unknown = 0,
    kind_firmware_entry,   // folder: os.ldr (+ info.txt without type, or type=firmware)
    kind_assembled_entry,  // folder: firmware entry whose info.txt has generation + needs_os, native info.txt only
    kind_official_tfw,     // file: the .tfw wrapper and crypto-header magics
    kind_ldr_image,        // file: an LDR stream (custom firmware or an XY Forever stage 1: not told apart)
    kind_xyf_stage2,       // file: an XY Forever stage 2 (XYS2 header)
    kind_zip,              // file: a ZIP (open-xy builder output): host only, refused on the device
    kind_mod_base,         // folder: info.txt type=mod-base
    kind_mod,              // folder: info.txt type=mod
    kind_content_pack,     // folder: a stock pack manifest.json and no info.txt
    kind_companion,        // refused: type=companion, or a companion image name
    kind_bootloader        // refused: a name that names a bootloader member
};
enum Reason : uint8_t {
    reason_ok = 0,
    reason_bad_name,       // a name that is not a safe single name (see valid_name)
    reason_short,          // a magic matched but fewer bytes were handed than the check needs
    reason_unknown,        // no rule matched
    reason_refused,        // kind_companion, kind_bootloader
    reason_host_only,      // kind_zip
    reason_need_manifest,  // the folder has info.txt but no parsed manifest was given
    reason_manifest,       // its info.txt did not parse (the manifest's status says why)
    reason_missing_image,  // a firmware entry without os.ldr
    reason_reserved_9,     // retired: kept to preserve reason numbering
    reason_content_set,    // content.sha256 without firmware.version or the reverse
    reason_ambiguous       // os.ldr and a pack manifest.json with no info.txt
};
struct Recognition { uint8_t kind, reason; bool accepted, content_set; };
// Bytes the file rules need at most (the .tfw crypto-header format words).
static const uint32_t prefix_bytes = 0x10c;
// 1-127 bytes of 0x20..0x7e, no '/', '\\' or ':', not "." or "..", not ending in '.' or ' '.
bool valid_name(const char* name);
// size: the whole file's size; prefix: its first prefix_length bytes.
void recognise_file(const char* name, uint32_t size, const uint8_t* prefix, uint32_t prefix_length,
                    Recognition& out);
// names: the folder's entries (files and folders, one level); info: its parsed info.txt, or 0.
void recognise_folder(const char* name, const char* const* names, uint32_t count, const Manifest* info,
                      Recognition& out);

// Dependency rules the device checks (010): exact matches only, no ranges, no solver.
enum Dependency : uint8_t {
    dep_ok = 0,
    dep_invalid,           // a manifest that did not parse ok
    dep_not_firmware,      // expected a firmware entry (type none or firmware)
    dep_not_base,          // expected type=mod-base
    dep_not_mod,           // expected type=mod
    dep_not_dependent,     // has no needs_os (not a base or an assembled entry)
    dep_no_sha256,         // the firmware entry has no sha256 key, so it cannot be matched
    dep_os_mismatch,       // its sha256 is not the needs_os
    dep_generation,        // generations differ (not the same kit export)
    dep_profile            // profiles differ
};
// A base or an assembled entry runs only on the firmware entry whose sha256 is its needs_os.
Dependency check_os(const Manifest& dependent, const Manifest& entry);
// A mod comes from the same kit export as its base (generation; profile when the mod names one).
Dependency check_mod(const Manifest& base, const Manifest& mod);
// An assembled entry was made from this base: same generation, needs_os and profile.
Dependency check_assembled(const Manifest& base, const Manifest& assembled);
// Apply: the base on the entry, then every mod on the base. failed: index of the failing mod,
// or count when the base/entry check failed (or dep_ok).
Dependency check_apply(const Manifest& entry, const Manifest& base, const Manifest* const* mods, uint32_t count,
                       uint32_t& failed);
// For remove (010 rule 5: list, do not refuse): does `dependent` name `entry`'s sha256?
bool needs(const Manifest& dependent, const Manifest& entry);
} }
#endif
