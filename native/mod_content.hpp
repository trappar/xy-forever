#ifndef XYF_MOD_CONTENT_HPP
#define XYF_MOD_CONTENT_HPP
#include "hash.hpp"
#include "mod_link.hpp"
namespace xyf_core { namespace mod_content {
// The content step of mod assembly (docs/storage/mods-assembly.md): a freestanding
// port of open-xy builder/core.mjs buildAssets for packages split by xy_forever/modkit.py. Into
// caller buffers: content/op1_factory.json, the official file (SHA-256 pinned) as one whole file
// with each listed machine's catalog.txt inserted at the three pinned offsets (an effect's "id"
// rewritten to its number, a player's to its number + 4), and files.sha256 (010 form, sorted): that
// file plus every package file the entry adds, content/display/<asset> and content/presets or
// aux_presets/..., each with its SHA-256 from the package's files.sha256. The job copies the listed
// package files (Builder::listed, sorted by path after ok) and checks their SHA-256; a content switch
// removes exactly them. Two machines with the same aux preset path (content/aux_presets/cv/...): the
// later machine in collection order wins, as core.mjs's last write does; it is listed once. Numbers
// and chosen extensions come from the linker's finished link (mod_link.hpp: number_at, extension_at).
// Every machine package must be one of the link's selection (the same Selection the link began with),
// its kind the slot its number has, the counts equal (else Status::unlinked). No listed package path
// may be in the firmware's official content list and no package id may name one of its presets or
// aux_presets folders (else Status::official). A catalog.txt is one object of at most max_entry bytes
// and max_entry_depth levels whose rewritten number is its top-level "id" key's value.
// Inputs are whole files in RAM. Steps of one package (step(): Status::more). No heap, no BSS, no
// library calls, no recursion, every loop bounded.
enum class Status {
    ok,
    more,
    argument,
    capacity,
    catalog,
    insert,
    entry,
    number,
    package,
    missing,
    duplicate,
    official,
    unlinked
};
enum class Kind : Byte { synth, drum, sample, effect, player, extension };
struct File {
    const char* path;
    hash::Digest sha256;
};   // one files.sha256 line
// One selected package, from its info.txt and files.sha256 (docs/storage/mods.md, "Mod packages").
struct Package {
    const char* id;                 // id=
    Kind kind;                      // machine=
    U32 order;                      // order= (collection order)
    const char* asset;              // asset=, 0 for "-"
    bool numbered;                  // catalog_number=<start>,<end>
    U32 number_start, number_end;
    U32 presets;                    // presets= (machines)
    const File* files;
    U32 file_count;
    const Byte* catalog_text;
    U32 catalog_bytes;   // catalog.txt in RAM (0 when the package has none)
};
// The base's catalog_sha256 and catalog_engine_at / _fx_at / _seq_at.
struct Catalog {
    hash::Digest sha256;
    U32 engine_at, fx_at, seq_at;
};
struct Limits {
    U32 packages, files;            // selected packages, files listed
    Limits() : packages(128), files(1024) {}
};
// Workspace for the tables: 4-byte aligned, scratch_bytes(limits) bytes (0: limits out of range).
U32 scratch_bytes(const Limits& limits);
// A vendor manifest must be a JSON object. If present its folder field must equal the on-disk pack ID.
bool pack_manifest(const Byte* data, U32 bytes, const char* folder);
// max_entry, max_entry_depth: the 0.0.6 kit's largest catalog.txt is 154 bytes, 2 levels deep.
static const U32 max_packages = 1024, max_files = 8192, max_catalog = 1u << 20, max_entry = 512, max_entry_depth = 4,
                 max_path = 160, detail_bytes = 32;
struct Listed {
    const char* path;
    U32 package;
    const hash::Digest* sha256;
};   // package ~0u: the catalog
// Caller-owned state of one run; the inputs passed to start() must stay alive until it ends.
struct Builder {
    const Catalog* pins;
    const Byte* catalog;
    const Package* packages;
    const Byte* official;
    const mod_link::Selection* selection;
    const mod_link::Workspace* link;
    const mod_link::Result* linked;          // the linker's (mod_link.hpp)
    Byte* out;
    Byte* list;
    U32 catalog_size, official_bytes, count, out_capacity, list_capacity, limit_packages, limit_files;
    Listed* listed;
    unsigned short* machines;
    U32* entry_bytes;
    U32 phase, cursor, listed_count, machine_count, extension_count, aux, aux_folders;
    U32 catalog_bytes, list_bytes;  // after ok: out[0, catalog_bytes) and list[0, list_bytes)
    const char* folders[16];
    hash::Digest catalog_sha256;
    Status failure;
    char detail[detail_bytes];      // which package, file or check refused
};
// out: content/op1_factory.json (capacity: the official size + every entry; 16 KiB is ample for
// 1.2.1). list: files.sha256 (67 + path bytes per file). official: the firmware's content.sha256
// (whole, sorted bytewise, paths relative to content/; read in place, never copied). selection,
// link, linked: the selection the link began with and the linker's finished link.
Status start(Builder& b, const Catalog& pins, const Byte* catalog, U32 catalog_size, const Byte* official,
             U32 official_bytes, const Package* packages, U32 count, const mod_link::Selection& selection,
             const mod_link::Workspace& link, const mod_link::Result& linked, Byte* out, U32 out_capacity, Byte* list,
             U32 list_capacity, const Limits& limits, Byte* scratch, U32 scratch_capacity);
Status step(Builder& b);
// One mod package object (whole, in RAM): no absolute symbols (SHN_ABS, named, not STT_FILE), which
// only the base may define. Status::package when it has one or is not a Blackfin ET_REL object.
Status check_object(const Byte* object, U32 size);
const char* status_name(Status status);
}}
#endif
