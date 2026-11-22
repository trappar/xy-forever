#ifndef XYF_MOD_LINK_HPP
#define XYF_MOD_LINK_HPP
#include "ldr.hpp"
// Device mod linker (docs/storage/mods.md, "Mod packages"):
// a freestanding port of open-xy builder/core.mjs readObject, numberLayout and linkKit (MIT,
// open-xy commit 52c8de9) with the selection checks of xy_forever/modkit.py rebuild_kit.
//
// It runs in steps of one whole file. next() names the file it needs (package "base", a machine
// id or "ext-<id>", and a path in it); the caller reads that file into RAM and passes it to
// step(). Order: the base's tables (identities.txt, the numbering rules, last), each chosen
// package's info.txt and objects.txt, then pass 1
// over every object of the base and the chosen packages in kit order (readObject's checks, names,
// sections), then steps with no file (an empty path: step(workspace, 0, 0, result)) that zero the
// module, zero_slice bytes each, then pass 2 over the linked objects in link order (bytes and
// relocations). Nothing
// points into an earlier file: what pass 2 needs is in the linker's own tables. The module goes
// straight into the caller's buffer (on the device: the image buffer at stock_bytes).
// No heap, no BSS, no library calls; the workspace is scratch_bytes(limits) of caller memory.
namespace xyf_core { namespace mod_link {

enum Slot { slot_engine = 0, slot_effect = 1, slot_player = 2 };
// One machine's numbers ({engine|effect|player, page}); has_* false = null.
struct Identity {
    U32 slot;
    bool has_number, has_page;
    int number, page;
};
static const U32 id_bytes = 16;          // machine id, NUL-terminated (core.mjs: 15 characters)
struct Number {
    char id[id_bytes];
    Identity identity;
};

// What to link; ids NUL-terminated, order irrelevant (as in linkKit). `layout`: the device
// Prior number reservations; has_layout false = none (null). The arrays must
// stay valid until the last step.
// `expanded`: the website's experimental 4 MiB option (core.mjs memoryKit(kit, true)): the module
// starts at the base's expanded_module_base and keeps its end, so the limit grows by the difference (4080
// KiB for 1.2.1); false = standard (module_base, module_bytes).
struct Selection {
    const char* const* machines;
    U32 machine_count;
    const char* const* extensions;
    U32 extension_count;
    const Number* layout;
    U32 layout_count;
    bool has_layout;
    bool expanded;
};

// Table bounds. Defaults hold the whole 0.0.6 kit (395 objects, 2,520 allocated sections, 3,065
// global names, 426 absent callbacks) with room to spare; scratch_bytes() is their cost.
struct Limits {
    U32 objects;        // objects in the base plus the chosen packages
    U32 sections;       // allocated sections of the objects that can be linked
    U32 names;          // distinct global symbol names (imports, exports, definitions, aliases)
    U32 records; // exported symbols of linkable objects, plus runtime members' imports (<= 65535: Object.records is 16-bit)
    U32 text_bytes; // those names, object paths, prefixes, service and claim words
    U32 machines, absent, fallbacks, numbers;
    Limits()
        : objects(640), sections(4096), names(4608), records(4096), text_bytes(128u * 1024u), machines(128),
          absent(1024), fallbacks(128), numbers(320) {}
};
U32 scratch_bytes(const Limits& limits); // 0 when a limit is out of range
// Caller-owned, 4-byte aligned, not shared between concurrent links; holds the tables until the
// next begin().
struct Workspace {
    Byte* scratch;
    U32 capacity;
};

enum class Status {
    ok,
    argument,
    table,
    limit,
    sequence,
    changed,
    // the selection and its packages (modkit rebuild_kit)
    duplicate_selection,
    no_machine,
    unknown_id,
    generation,
    package,
    order,
    // openKit
    collection,
    numbers_request,
    fixed_differs,
    accessor,
    processor_accessor,
    // readObject
    object_size,
    object_type,
    section_table,
    section_size,
    section_file,
    string_offset,
    string_end,
    utf8,
    section_flags,
    section_kind,
    symbol_tables,
    symbol_table,
    symbol_section,
    rel,
    relocation_table,
    relocation_target,
    relocation_type,
    relocation_range,
    relocation_overlap,
    // linkKit and numberLayout
    claim,
    both_hold,
    layout_fixed,
    forks,
    layout_invalid,
    no_free,
    no_valid,
    weak,
    runtime_replaces,
    capacity,
    empty,
    symbol_outside,
    duplicate_global,
    absent_extension,
    absent_machine,
    service_fallback,
    unresolved,
    overflow,
    branch,
    // device bounds core.mjs does not have: a name over 255 bytes; too many names in one hash chain
    name_length,
    name_chain,
    // device rules for packages anyone can copy to the device: an absolute symbol in a
    // package object (detail: the object); a package object importing another package's symbol (the
    // name); over 16 claims in one extension (its id); a NUL byte in a text table (the path); an
    // info.txt value too long to keep (the key)
    absolute,
    foreign_import,
    claim_count,
    text_nul,
    text_length
};
struct Sizes {
    U32 initialized, bss, allocated, available;
}; // linkKit's sizes
static const U32 detail_bytes = 33;
static const U32 zero_slice = 128u * 1024u; // module bytes one zero step clears
// linkKit's return value. module = the caller's buffer, sizes.initialized bytes; globals,
// aliases, objects, numbers and service names are read with the functions below (segments are
// not kept). A refusal is `status` plus `detail` (<= 32 bytes: the name, id or number core.mjs
// names) and `step` (the step that refused).
struct Result {
    Status status;
    char detail[detail_bytes];
    U32 step;
    U32 module_base, limit; // limit = moduleBytes(profile) (base info.txt module_bytes; expanded: + the
    // difference of module_base and expanded_module_base)
    Sizes sizes;
    U32 services; // bit i: services.txt row i is active (linkKit services)
    U32 extensions; // bit i: extensions.txt row i is linked (linkKit extensions)
    U32 objects, numbers, globals, aliases; // counts
    U32 object_bytes; // object bytes passed to step(), both passes
};
// `module`: at least moduleBytes(profile) bytes (2,080,768 for 1.2.1); only the first
// sizes.initialized are written.
Status begin(const Selection& selection, const Limits& limits, Workspace& workspace, Byte* module, U32 module_capacity,
             Result& result);
struct Request {
    const char* package;
    const char* path;
}; // path "": no file (a zero step)
bool next(const Workspace& workspace, Request& request); // false: finished (see result.status)
Status step(Workspace& workspace, const Byte* data, U32 size, Result& result);

// After an ok link: globals.get(name); entry i of the name table (kind 0 import only, 1 global,
// 2 global from definitions or number symbols, 3 alias); linked object i (link order); number i
// (the layout after: fixed, layout, new); services.txt row i; extensions.txt row i (its id).
// The patch and content steps (mod_patch, mod_content) read the result through these.
bool global(const Workspace& workspace, const char* name, U32& value);
bool name_at(const Workspace& workspace, U32 index, const char*& name, U32& length, U32& value, U32& kind);
bool object_at(const Workspace& workspace, U32 index, const char*& name, U32& length);
bool number_at(const Workspace& workspace, U32 index, Number& number);
bool service_at(const Workspace& workspace, U32 index, const char*& name, U32& length);
bool extension_at(const Workspace& workspace, U32 index, const char*& name, U32& length);
// How much of each table the last link used (for sizing Limits).
struct Usage {
    U32 objects, sections, names, records, text_bytes, absent, fallbacks, numbers;
};
void usage(const Workspace& workspace, Usage& out);
}}
#endif
