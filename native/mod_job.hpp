#ifndef XYF_MOD_JOB_HPP
#define XYF_MOD_JOB_HPP
#include "mod_content.hpp"
#include "mod_link.hpp"
#include "mod_patch.hpp"
namespace xyf_core { namespace mod_job {
// The device's mod assembly job (plans 5.4d1a, 5.4d1b; decision 019): the linker, patch and content
// steps (mod_link, mod_patch, mod_content) driven in small bounded steps inside one caller-given work
// region, every file read and written through a table of file calls (on the device: processor B's
// file client, one 1 KiB request per call, usb_service's mods.apply; 5.4d2). Same inputs as host/mod_assemble.cpp's one-shot run, same
// bytes out.
//
// Inputs, each read whole into the region and checked against its package's files.sha256 when it
// belongs to a package: <generation>/{base,<machine>,ext-<id>}/files.sha256, info.txt, catalog.txt
// (machines), the base's edits.bin, every file the linker asks for, every content file the entry needs;
// the stock os.ldr (read straight into the image buffer; the patch pins its SHA-256), the stock
// op1_factory.json (the content step pins it) and the firmware's content.sha256.
// Outputs: one whole library entry in the existing folder `out` (docs/STORAGE.md): every content/...
// file of mod_content's list (the packages' display and preset files copied, content/op1_factory.json
// built), layout.json (opxy-layout/1 as open-xy's website writes it: profile, selected, extensions,
// machines = the link's numbers), info.txt (type=firmware, an assembled mod set: name, kind open-xy,
// bytes, crc32, sha256, needs_os, generation, profile), files.sha256 (every file above and os.ldr,
// sorted), then os.ldr. The job makes the folders under `out` it needs (mkdir). Each output is written
// as <name>.part and read back and compared by SHA-256; os.ldr is read back whole, its SHA-256 must be the
// assembly's (the image the patch's restore check passed), and it must pass mod_patch::start_verify's
// layout checks and the policy (`hashed`: the restore hashes are not repeated); then
// every part is renamed onto its name, os.ldr last, so an entry with an os.ldr is complete. A failed
// or cancelled job removes every name it may have written (parts and finals; folders stay), and the
// four fixed names always; it never leaves an os.ldr.
//
// One step() is one of: one read or write call of at most chunk_bytes, one linker step (one whole
// object, at most 1 MiB, or one 128 KiB zero slice), one patch or image-check step (128 KiB hashed or
// one pass over the edits), 128 KiB of CRC-32, one content step (one package), the entry texts, one
// output's folders and open, one rename. No heap, no BSS, no library calls.

// File calls. Paths are NUL-terminated, at most path_bytes with the NUL. A handle is >= 0.
struct Files {
    void* context;
    int (*size)(void* context, const char* path, U32* bytes);               // 0, or < 0 (absent)
    int (*open)(void* context, const char* path, bool write);               // write: create or truncate
    int (*read)(void* context, int handle, Byte* data, U32 bytes);          // bytes read (0: end), or < 0
    int (*write)(void* context, int handle, const Byte* data, U32 bytes);   // bytes written, or < 0
    int (*close)(void* context, int handle);                                // 0, or < 0
    int (*rename)(void* context, const char* from, const char* to);         // 0, or < 0 (`to` is absent)
    int (*remove)(void* context, const char* path);                         // 0, or < 0 (absent included)
    int (*mkdir)(void* context, const char* path);                          // 0: the folder exists now (made
                                                                            // or already there), or < 0
};
// The selection and the files, as host/mod_assemble.cpp's job; everything must stay valid until the
// job ends.
struct Input {
    mod_link::Selection selection;   // machines (layout.json "selected" keeps their order), extensions, layout,
                                     // memory (expanded: the 4 MiB option, 4.8m1; layout.json then records
                                     // "memory": "expanded" and its reserved_bytes)
    const char* generation;          // folder holding base/, <machine>/, ext-<id>/
    const char* stock;               // stock os.ldr
    const char* catalog;             // stock content/op1_factory.json
    const char* official;            // the firmware's content.sha256
    const char* out;                 // output folder; it exists (the job makes the folders under it)
    const char* name;                // info.txt name: 1-47 printable ASCII bytes, no space at either end
    const Policy* policy;            // ldr::inspect's policy (the cold LDR policy)
    bool profiling;                  // false: the website's mode
    bool unverified;                 // the user confirmed a base that is not a known one
};
enum class Phase { idle, inputs, link, patch, content, write, verify, finish, done, failed, cancelled };
enum class Error {
    none,
    argument,    // start(): a missing input, file call or region, an unaligned region, too many packages
    path,        // a composed path over path_bytes
    region,      // the work region is too small for what comes next
    open,        // size() or open() failed: the file is absent or unreadable
    read,        // read() or a read handle's close() failed
    truncated,   // the file ended before its size
    size,        // a file over its bound (stock larger than the image buffer)
    list,        // a files.sha256 line is not "<64 hex>  <path>"
    unlisted,    // a file the job needs is not in its package's files.sha256 (or the package is unknown)
    sha256,      // a file differs from its files.sha256 entry
    info,        // a package info.txt field (catalog_number)
    base,        // the base info.txt: patch facts or catalog offsets (mod_patch::read_profile), no edits.bin, or a base that is not a known one and not confirmed (status: mod_patch unverified)
    link,        // the linker refused (status: mod_link::Status)
    patch,       // the patch refused (status: mod_patch::Status)
    content,     // the content step refused, or a mod object failed check_object (status: mod_content::Status)
    write,       // open, write or close of an output failed
    rename,      // removing or renaming an output failed
    verify,      // an output read back differs from what was written
    cancelled,
    image        // os.ldr read back fails start_verify or differs from the assembly (status: mod_patch::Status)
};
static const U32 chunk_bytes = 64u * 1024u, path_bytes = 240, detail_bytes = 33,
                 catalog_bytes = 16384, list_bytes = 65536, layout_bytes = 8192, info_bytes = 1024,
                 crc_slice = 128u * 1024u;
// State of one job; caller-owned (no BSS), reusable: start() resets it.
struct Job {
    struct Package {                 // one package as the job holds it
        const char* name;            // "base", the machine id, "ext-<id>"
        Byte* list; U32 list_bytes;  // files.sha256, each line's end turned into a NUL
        mod_content::File* files; U32 file_count;   // its lines, info.txt excluded
        const hash::Digest* info_sha256;   // info.txt's line (0: not listed)
        Byte* info; U32 info_bytes;
        Byte* catalog; U32 catalog_bytes;
    };
    struct Load {                    // the file being read
        int handle; Byte* data; U32 size, done; const hash::Digest* want;
        hash::Sha256 sha;
        bool active, ready, stream, back;
    };
    const Input* input; const Files* files;
    Byte* region; U32 region_bytes, low, peak;   // region[0, low) is held; peak: the most ever used
    Phase phase; Error error; U32 status;        // status: the refusing step's own Status, as a number
    char detail[detail_bytes];                   // which file, object, edit or check
    U32 stage, index, steps, work, done, total;  // work: bytes handled by the last step
    Package* packages; U32 package_count;
    mod_content::Package* content_packages;
    Load load;
    int out_handle;
    U32 out_index, out_done;
    U32 out_count, out_written;                  // outputs (content files + 4); how many were opened
    U32* out_sizes;                              // each output's size (a content file's once loaded)
    const Byte* source;                          // the content file being written (loaded from its package)
    bool check_object;                           // the file being read is a mod object (check_object)
    Byte* spare; U32 spare_bytes;                // where a link file may go instead of region[low...) (step_link)
    char path[path_bytes];
    // the assembly
    mod_patch::Profile profile; mod_content::Catalog pins;
    U32 module_limit, reserved_bytes;            // the layout in use (mod_patch::layout; 4.8m1 expanded or not)
    Byte* image; U32 image_bytes;
    const Byte* edits; U32 edits_bytes;
    Byte *stock_catalog, *official, *link_scratch, *patch_scratch, *content_scratch, *catalog_out, *list_out;
    U32 stock_catalog_bytes, official_bytes;
    mod_link::Workspace workspace; mod_link::Result linked;
    mod_patch::Patcher patcher; mod_content::Builder builder;
    Byte *layout_out, *info_out;
    U32 layout_size, info_size, list_size, ldr_bytes, crc, crc_done;
    hash::Digest ldr_sha256, layout_sha256, info_sha256, list_sha256;
    U32 link_pass;                               // 8.8 timing: the link's last step: 1 tables/pass 1, 2 zero, 3 pass 2
};
// 8.8: the part of the job a step belongs to, finer than Phase (the save-time log, target/xyf_save_log.hpp).
enum TimingPart : U32 {
    tp_base, tp_packages, tp_catalog, tp_stock, tp_patch1, tp_link1, tp_zero, tp_link2, tp_set_module, tp_patch2,
    tp_crc, tp_content, tp_write, tp_verify, tp_check, tp_finish, tp_count
};
U32 timing_part(const Job& job);
// Starts a job over `region` (4-byte aligned; the device gives 8 MiB - 64 KiB at 0x8b010000; peak with the
// 0.0.6 packages: 5.5 MiB standard, 7.4 MiB with the 4 MiB layout: the image buffer is kept last and the link's
// files go into the module's unused part, running on into the free region above it). Error::none, or a
// refusal (the job is then failed). Nothing is read or written yet.
Error start(Job& job, const Input& input, const Files& files, Byte* region, U32 region_bytes);
// One bounded step; returns the phase after it (done, failed and cancelled are final).
Phase step(Job& job);
// Stops a running job: closes its handle and removes every output name; the phase becomes cancelled.
void cancel(Job& job);
// Progress of the current phase: done of total units (total 0: not known ahead). inputs: bytes read;
// link, patch: steps (the CRC-32 slices included); content: packages; write, verify, finish: outputs
// written, read back (os.ldr's image check included), renamed.
struct Progress { Phase phase; Error error; U32 status, done, total; };
void progress(const Job& job, Progress& out);
const char* error_name(Error error);
const char* phase_name(Phase phase);
} }
#endif
