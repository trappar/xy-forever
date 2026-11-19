#ifndef XYF_FILE_ENGINE_HPP
#define XYF_FILE_ENGINE_HPP
#include <stdint.h>
// The file engine's path policy and request decision (docs/architecture/link.md).
// Pure and freestanding (no heap, no library calls), host-tested.
//
// One function decides every path: decide_engine. Processor A applies it to every request it
// serves (storage_server.cpp serve_engine, through decide_request); processor B applies it to
// every path it builds before sending one (storage_server.cpp client_read_file,
// valid_write_path; usb_service.cpp's storage self-test), so B refuses early and A refuses
// again if B is wrong.
//
// Project rule: allow anything that can be recovered from without external hardware.
// Refused (everything else is allowed, including deleting XY Forever's own files; only A's own
// restore_marker re-creates a marker that vanished during an operation):
//  - a path that does not parse (fail closed): none, no NUL within max_path bytes (239 + NUL:
//    the stock path buffer), a "." or ".." name, a control byte (below 0x20, or 0x7f);
//  - anything outside /yaffs2: the path must start with "/yaffs2" followed by '/' or its end
//    (relative paths, "/fat32" (the stock update RAM disk), "/yaffs20", "/YAFFS2" are outside);
//  - the write rule (since 1.13): everything on /yaffs2 may be changed
//    except (1) /yaffs2/bootloader.dev, never changed, replaced, renamed or deleted, nothing renamed onto
//    it, and (2) the stock updater's inbox /yaffs2/upgrade: nothing written, created, renamed into or
//    deleted inside it, it is not removed, renamed or made, nothing renamed to it. So: a write open, unlink,
//    rmdir, mkdir and rename of /yaffs2 itself, the marker or the inbox (and below it), renaming onto
//    /yaffs2, the marker or into the inbox. Both stay readable. Through links: storage_server
//    reaches_protected (A, every change by hand). bootloader.dev is the 1.2.1 bootloader's dev-menu
//    marker (88a1d7e6), the device's recovery path: nothing known recreates it once XY Forever is
//    gone too (docs/SAFETY.md). Its name is compared with ASCII case ignored. yaffs here is
//    case-sensitive, so this over-refuses (/yaffs2/BOOTLOADER.DEV is another file that cannot be
//    removed): harmless, keep it. The real marker is the lowercase name (88a202b0), which is
//    what a re-creates (storage_server.cpp marker_path); do not "fix" one to match the other;
//  - every raw boot-partition operation (the boot partition is written by INSTALL only);
//  - a streamed write open (op_stream, #4) outside /yaffs2/xy-forever/tmp/;
//  - the depth rule (#124, docs/SAFETY.md): no folder made more than max_folders below /yaffs2 and no
//    file written, created or renamed into one (refuse_depth): after the stock dev-menu MTP (10-entry
//    stacks, 889f1ff6) has walked a file 7 folders deep, Track 5 in that boot hangs and the next boot
//    needs a storage repair; 8 cannot be read or deleted, 9 crashes it. Reads, listing and removals stay allowed
//    at any depth, so an older deep tree can be deleted. A rename checks both names as files (A's
//    rename_from sees only the source); a folder target, and a folder moved deeper, are B's check
//    (file_client::rename walks the folder).
// Spelling: '/' runs count as one and a trailing '/' is ignored before the names are compared,
// so "/yaffs2//BOOTLOADER.DEV/" is the marker. Bytes 0x80..0xff pass (UTF-8 names: the device
// compares bytes and does not check UTF-8).
namespace xy_forever { namespace file_engine {
static const uint32_t max_path = 240;                  // bytes with the NUL (stock path buffer)
static const uint32_t max_folders = 6;                 // folders below /yaffs2 a file may sit in (#124)
// Stock slave request classes (the factory 889f8f38) and the stock slave path body (889f8d94).
static const uint32_t partition_class = 0x11907694u, path_class = 0x119076b4u, read_class = 0x11907368u,
                      sector_class = 0x11907348u, write_class = 0x11907710u, query_class = 0x11907388u;
static const uint32_t path_buffer = 0x8803f6e0u;
// file.rename's two paths reach A as two path-class access requests (op 0x21, the stock path
// builder 889f8d22 on B), told apart from an access by their argument word: rename_from carries the
// source (A keeps it), rename_to the destination (A renames). A pairs a rename_to only with a
// rename_from that was the request just before it (source below; 0: none).
// rename_over (#4) is rename_to that may replace an existing file: its source must be an upload
// (upload_size below), A checks the source's size against its name and the target is a file or absent.
static const uint32_t rename_from = 0x52464e52u, rename_to = 0x4f544e52u;   // "RNFR", "RNTO" (LE)
static const uint32_t rename_over = 0x4f545052u;                          // "RPTO" (LE)
// #144: A's request timing (storage_server_timing.ipp), read by B as a path-class access too (path /yaffs2):
// argument timing_tag | k answers words 2k and 2k + 1 of A's counters as the reply's value and errno; k =
// timing_clear zeroes them. A answers it itself: nothing reaches storage, and the policy never sees it.
static const uint32_t timing_tag = 0x4d495400u, timing_clear = 0xffu, timing_words = 12;   // "\0TIM" (LE) | k
enum Operation : uint32_t {
    op_read = 1,        // open for reading (flags 0)
    op_list,            // open a folder
    op_stat,            // stat, file-system space, access
    op_write,           // open with any write, create or truncate flag
    op_stream,          // open a streamed write (#4: only under /yaffs2/xy-forever/tmp/)
    op_mkdir,
    op_unlink,
    op_rmdir,
    op_rename,          // path -> second
    op_raw              // a raw boot-partition operation (no path)
};
enum Verdict : uint32_t {
    allow = 0,
    refuse_path,        // does not parse: none, too long, ".", "..", a control byte
    refuse_outside,     // not under /yaffs2
    refuse_protected,   // changes /yaffs2, /yaffs2/bootloader.dev or /yaffs2/upgrade (see above)
    refuse_tmp,         // a streamed write open outside /yaffs2/xy-forever/tmp/
    refuse_raw,         // the raw boot-partition layer
    refuse_operation,   // not an operation (or a second path where none belongs)
    refuse_depth        // the depth rule (above): more than max_folders folders below /yaffs2
};
// The path policy. second: op_rename's target, else 0.
Verdict decide_engine(Operation op, const char* path, const char* second = 0);
// An upload's temporary file (#4): exactly /yaffs2/xy-forever/tmp/put-SSSSSSSS-TTTTTTTT (8 lowercase hex
// digits each: S the file's size, T a tag: the CRC-32 for file.write_*, the object handle for MTP). size: S.
bool upload_size(const char* path, uint32_t& size);
// Writes the name for size and tag into out (upload_bytes with the NUL).
static const uint32_t upload_bytes = 45;
void upload_name(char* out, uint32_t size, uint32_t tag);
// The names below /yaffs2 in path (a file's folders are this minus one); 0xffffffff when it does not parse.
uint32_t depth(const char* path);

// The raw boot-partition layer as stock requests: partition class 4 / 0x14 trim / 0x18 select,
// sector class 0x11 / 0x12 reads, write class 0x15 / 0x16 writes.
bool raw_request(uint32_t vtable, uint32_t op);
// A's decision for one stock request in engine mode (codes 0x60.. as logged in the report;
// xy_forever/census.py ENGINE_ACTIONS).
enum Action : uint32_t {
    refuse_off = 0x60,  // no engine entry yet, or its stock code check failed
    refuse_class, refuse_op,
    refuse_raw_layer,   // decide_engine: refuse_raw
    refuse_bad_path,    // not in the stock path buffer, or decide_engine: refuse_path, refuse_depth
    refuse_outside_path, refuse_protected_path, refuse_tmp_path,   // decide_engine
    refuse_mount,       // no storage session (op 8 first)
    refuse_read_only,   // a change without the read-write window
    refuse_handle,      // a file or folder handle a did not open for b
    refuse_full,        // a's handle table is full
    run_mount, run_unmount,
    run_file,           // partition 0x2c lseek, 0x2d close (by file handle)
    run_closedir,       // partition 0x30
    run_open,           // path 0x29
    run_opendir,        // path 0x2e
    run_path,           // path 0x21 access, 0x25 unlink, 0x26 mkdir, 0x27 rmdir
    run_query,          // query 0x22 stat, 0x23 statfs, 0x31 free space
    run_read,           // read 0x2a
    run_readdir,        // sector 0x2f
    run_write,          // write 0x2b
    refuse_partition,   // a boot partition is selected (install mode): yaffs is not on the chip's
                        // partition register, so nothing may reach storage until it is 0 again
    run_rename_from,    // path 0x21 rename_from (A keeps the source; nothing runs)
    run_rename_to,      // path 0x21 rename_to (A renames source -> this path: a change)
    run_rename_over     // path 0x21 rename_over (#4: an upload replaces this path, or takes it: a change)
};
// What a knows when it decides (each nonzero = true). session: a's storage session for this
// entry is mounted; window: the read-write window is open; partition: install mode has a boot
// partition selected (the stock select 88a08710 switches the chip's partition register).
struct State { uint32_t on, session, window, fd_known, fd_space, dir_known, dir_space, partition; };
// Every request: the raw layer first (whatever the state), then a selected boot partition (every
// request refused, op 8 and op 9 too), then class and op, then the path policy for path
// requests, then op 8 / op 9, then session, window, handles. argument = word +8
// (open flags for 0x29, the handle for handle requests, rename_from / rename_to / rename_over for 0x21). source:
// the armed rename source (op_rename with this path as its target), else 0. Not exposed (refuse_op): truncate 0x36,
// set times 0x35, fsync 0x34 / 0x28.
Action decide_request(uint32_t vtable, uint32_t op, uint32_t argument, uint32_t path_pointer, const char* path,
                      const State& state, const char* source = 0);
} }
#endif
