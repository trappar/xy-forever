#ifndef XYF_FILE_CLIENT_HPP
#define XYF_FILE_CLIENT_HPP
#include <stdint.h>
// Processor B's file client (docs/architecture/link.md): B's own requests to
// A's file engine, each one ending within a time limit. It replaced B's 12 stock posix client
// calls, which submit through 889f869e -> 889f7b12 and spin on the request's done byte with no
// limit (a silent A froze B for good; the builder now forbids both entries).
//
// One request = one stock request builder on the boot session's single request slot
// (peer_services Stock121PeerSession::request_slot), submitted as 889f7b12 does but polled from
// here with a bound (xyf_boot link_claim / link_step / link_sent). The bytes on the wire are the
// stock client's: the same builders with the same arguments (1.2.1 role-1 client helpers):
//   partition 889f87d4 (already a peer call): 8 mount, 9 unmount, 0x2c lseek, 0x2d close, 0x30 closedir
//   path      889f8d22: 0x29 open (flags), 0x26 mkdir (mode), 0x2e opendir; the path's pointer at +0x18
//   query     889f8e36: 0x22 stat (0x34-byte record), 0x23 statfs (0x0c bytes)
//   read      889f6c5c: 0x2a read (<= chunk bytes)
//   write     889f8c10: 0x2b write (<= chunk bytes)
//   readdir   889f88ac: 0x2f readdir (one 0x10c-byte entry)
// Bounds: request_ms per request, change_ms for the ones that may change storage or wait for a
// read-write mount or checkpoint (op 8, op 9, mkdir, unlink, rmdir, a write open, write, close, the
// rename_to access). Past its bound the
// caller gets link and the request stays out (the slot is busy: later calls get busy, also link to
// a host) until A answers or xyf_boot::lost_ms passes (then the link is lost until a reboot; the
// menu says so and USB still answers).
// Every path passes the engine's policy (file_engine::decide_engine) before anything is sent;
// A applies it again to every request. Paths, request and reply bodies live only in the client's
// own buffers (path_buffer, data_buffer), so an answer that arrives after its caller gave up can
// only land there.
namespace xy_forever { namespace file_client {
static const uint32_t request_ms = 5000, change_ms = 45000;
static const uint32_t chunk = 1024;                                  // bytes per read or write request
static const uint32_t record_bytes = 0x34, space_bytes = 0x0c, entry_bytes = 0x10c;
static const uint32_t mounted_stale = 2, emfile = 24;                 // read_file; A's errno EMFILE
// b session scratch (DMA-able): the path (240 bytes with the NUL) and one request or reply body. #144: the body
// buffer holds piece_max, the stock client's own piece (its fflush 889f6f54 writes at most 0x1000 per 0x2b; A's
// read executor streams 0x1000 per body), in the free 4 KiB below log_frame (usb_service.hpp: rx_chunk ends at
// 0x8b80c800); read and write take up to piece_max, every other caller still moves chunk.
static const uint32_t path_buffer = 0x8b80f000u, data_buffer = 0x8b80d000u, data_capacity = 0x1000u;
static const uint32_t piece_max = data_capacity;
static_assert(data_capacity >= chunk && data_capacity >= entry_bytes, "one body fits the client's buffer");
enum Outcome : uint32_t {
    done = 0,       // A answered: value and error are its reply (+0x10, +0x14)
    refused,        // B's policy check (or a malformed call): nothing was sent
    link,           // A did not answer within the bound; the request is still out
    busy,           // an earlier request is still out
    lost            // the link is lost (until a reboot)
};
struct Reply {
    uint32_t outcome;
    int32_t value;
    uint32_t error;
};
// The bound of one request (pure; host-tested): partition class op, or path op with its argument.
uint32_t bound_ms(uint32_t op, uint32_t argument);
// The USB status for an outcome other than done (refused, else link).
uint32_t status(uint32_t outcome);
// Pure; host-tested. The USB status of A's failed unlink, rmdir or mkdir by its error number
// (the device's yaffs numbers, 1.2.1 listing 889fa4b0): 2 not_found, 13 (A's policy) refused, 93
// (a folder not empty) state, any other storage.
static const uint32_t enoent = 2, eacces = 13, enotempty = 93;
uint32_t change_status(uint32_t error);
// Pure; host-tested: file.rename's status by A's error number: 17 (the target exists; A refuses
// it before the stock rename, which would replace it) state, else as change_status.
static const uint32_t eexist = 17;
uint32_t rename_status(uint32_t error);
// One file.list entry from a stock readdir body (yaffs_dirent: type +10, name +11) and, when
// have_record, its stock stat record (mode +8: 0x4000 folder, 0x8000 file; size +0x1c): {u8 kind
// (0 file, 1 folder, 2 other), u32 size (0 unless a file), u8 name bytes (<= 255), name}. The bytes
// written, 0 when it does not fit in room.
static const uint32_t max_name = 255;
uint32_t list_entry(const uint8_t* entry, bool have_record, const uint8_t* record, uint8_t* out, uint32_t room);
// folder + '/' + name into out (file_engine::max_path bytes); false when it does not fit.
bool child_path(const char* folder, const char* name, char* out);
// What one readdir answer means (pure; host-tested). Stock 889f6bfa..889f6c02 (1.2.1, A's
// sector executor 889f6b7c, op 0x2f): the reply value is A's yaffs_readdir result, the address of
// A's yaffs_dirent (any nonzero word, high bit set on the device), 0 at the end of the folder; the
// error word at the end is the stock posix errno (0x8807da98), which stays from earlier calls, so it
// says nothing. A's own refusal of a readdir answers -1 (storage_server, with a zeroed entry). Build
// 165 took only 1 as an entry: every folder listed empty on the device. The MTP self-test and
// file.list both read entries through this.
enum Folder : uint32_t { folder_entry = 0, folder_end, folder_error };
uint32_t readdir_result(int32_t value);
// CRC of the stock code the client calls (the builders, the master's request path, the class
// tables); 0 = ok. The same spans as the peer session's identity, which also checks them on every
// poll; B's USB stays off when they differ (usb_service usb_off 2).
uint32_t identity();
#if defined(__bfin__)
// Requests (role 1, the home loop's session). value: the stock result (mount: 0 = mounted; unmount:
// 1 = done; open: fd; opendir: folder handle, 0 = none; readdir: readdir_result).
Reply mount();
Reply unmount();
// Op 9 without waiting in it (the window close at the mod job's end; yaffs's
// checkpoint took 0.3 s to about 17 s on the device), so B's loop keeps answering USB meanwhile.
// unmount_start: sent (done), or why not (busy, lost). unmount_poll, once per pass of B's loop: busy
// while A has not answered within the bound (change_ms); then done (A's value), link (past the bound;
// the request stays out, as any request past its bound) or lost. refused: none was started.
Reply unmount_start();
Reply serving_start(uint32_t& expected_ack); // command_item, asynchronously: no filesystem operation
Reply unmount_poll();
// #144: the client leaves at most one request out for its owner (unmount_start, mkdir_start: busy while one is
// left). Every other request collects it first (its claim waits, bounded, for the slot) and keeps its answer
// for the owner, so neither the slot's reuse nor the next answer can take it. left_poll: the left request's
// answer, kept or as unmount_poll (wait: awaited up to its bound); left_out: one is left.
Reply left_poll(bool wait);
bool left_out();
// #144: write (op 0x2b) left out: B's loop goes on (USB, the next piece) while A writes; left_poll collects A's
// answer (the next write_start waits for it through its claim). busy while one is left.
Reply write_start(int fd, const uint8_t* data, uint32_t count);     // count <= piece_max
// #144: read (op 0x2a) left out into the client's read-ahead buffer at stream_buffer + at, so B's loop goes on
// (USB sends the bytes read before) while A reads; left_poll collects A's answer (value: the bytes read, then at
// stream(at, value); short only at the end of the file). busy while one is left. The buffer is the client's, as
// data_buffer is (an answer that arrives after its owner gave up lands only there): the top stream_capacity bytes
// of the MTP session's slot (usb_service_mod_job.ipp mtp_slot_bytes; the Session stays below it), reserved only in
// MTP builds, whose session is its only user.
static const uint32_t stream_buffer = 0x8b7fc000u, stream_capacity = 0x4000u;
// bytes per read_start: A streams them back in 0x1000-byte bodies, its stock read executor (889f6cd6) re-posting
// itself after each (889f66fc) and B's stock master taking each body into place, then the done byte: the stock
// fread's own shape (the dev menu's GetObject asks 16 KiB per request; the OS 64 KiB). Two halves of the buffer.
static const uint32_t stream_max = stream_capacity / 2;
static_assert(stream_max % 0x1000 == 0, "whole bodies");
Reply read_start(int fd, uint32_t at, uint32_t count);             // at + count <= stream_capacity, at % 32 == 0
const uint8_t* stream(uint32_t at, uint32_t count);                 // after an answer: those lines invalidated
// 193: op 0x26 (mkdir; EEXIST is fine) without waiting in it: A's first change opens its read-write window,
// which after an unclean shutdown may take seconds; unmount_poll (the same slot, bound change_ms) ends it.
Reply mkdir_start(const char* path);
Reply open(const char* path, uint32_t flags);
Reply read(int fd, uint8_t* out, uint32_t count);                   // count <= piece_max
Reply write(int fd, const uint8_t* data, uint32_t count);           // count <= piece_max
// #144: A's request timing words 2k and 2k + 1 (value, error), or k = file_engine::timing_clear zeroes them.
Reply a_timing(uint32_t k);
// #7: only the dedicated read-only diagnostic uses these. A gates every raw request independently.
Reply boot_probe(uint32_t operation);
Reply tfw_probe(const char* path, uint32_t operation);
#if defined(XYF_TFW_CHECK)
// Submit one of tfw_hash_rpc's commands. The owner collects A's reply with left_poll(false)
// and uses the same immutable path for the whole hash job and the subsequent package read.
Reply tfw_hash_send(const char* path, uint32_t operation);
#endif
Reply boot_select(uint32_t partition);
Reply boot_sector(uint32_t sector, uint8_t* out);
Reply seek(int fd, int offset);                                     // SEEK_SET
Reply close(int fd);
Reply mkdir(const char* path);
Reply stat(const char* path, uint8_t* record);                      // record_bytes
// Check a literal directory entry after a stat follows a link and reports ENOENT.
// A scan owns at most one directory. scan_again asks the caller for another step.
static const uint32_t scan_again = 0xffffffffu;
struct NameScan {
    uint32_t folder, seen;
};
uint32_t scan_name(const char* path, NameScan& scan, volatile uint32_t& mounted);
uint32_t close_name_scan(NameScan& scan, volatile uint32_t& mounted);
Reply statfs(const char* path, uint8_t* space);                     // space_bytes
Reply opendir(const char* path);
Reply readdir(uint32_t folder, uint8_t* entry);                     // entry_bytes
Reply closedir(uint32_t folder);
Reply unlink(const char* path);                                     // op 0x25
Reply rmdir(const char* path);                                      // op 0x27
// The USB service's file commands (xyf::usbp statuses; link when A did not answer).
// Before each one, is A still XY Forever? command_item as one bounded request (request_ms;
// past it link, the request left out as any other): only A's XY Forever loop answers it with
// command_ack (A's bootloader, after a lone restart, answers the select 0 and has no path policy).
// s_ok, else s_link. It also latches A's read entry, as the menu's command item did.
uint32_t serving();
// READ_FILE: whole file (ranged false: <= capacity, else s_too_large) or up to capacity bytes from
// offset (fewer = end of file). mounted: B's op-8 state across calls (mounted_stale: a request of
// an earlier read ran out its bound, so A may hold a handle B never closes: op 9 before the next
// open). An open A refuses with EMFILE (its handle table is full) is s_storage, not s_not_found.
// left: the end of the read is the caller's, after its USB reply, so A's last requests overlap
// the host's next request (this pays for the usb_service per-command check): the file read_file
// opened (fd -1: none) and whether a ranged read's 1-byte probe is still due before the close.
struct Open {
    int fd;
    bool probe;
};
uint32_t read_file(const char* path, uint32_t offset, bool ranged, uint8_t* out, uint32_t capacity, uint32_t& bytes,
                   volatile uint32_t& mounted, Open* left = 0);
void finish_read(const Open& left, volatile uint32_t& mounted);     // probe, close; link: mounted_stale
// LIST: one page of a folder (opendir, readdir past skip entries, then entries with a stat
// each while they fit capacity and max_page, closedir); more = 1 when the page ended before the
// folder did. mounted as read_file. A folder A cannot open is s_not_found (EMFILE: s_storage); a
// readdir A refused (folder_error) is s_storage after the closedir: never an ok, short page.
static const uint32_t max_page = 64;
uint32_t list(const char* path, uint32_t skip, uint8_t* out, uint32_t capacity, uint32_t& count, uint32_t& more,
              uint32_t& bytes, volatile uint32_t& mounted);
void release(volatile uint32_t& mounted);                           // op 9 when mounted
// B's op-8 state ready for raw requests (the mod job's file calls): a stale mount closed first (op 9),
// then op 8 when not mounted. s_ok, else the status (link, storage).
uint32_t mount_ready(volatile uint32_t& mounted);
// FILE_WRITE (Test 049/050): op 8 (A opens the read-write window), mkdir for every folder below
// /yaffs2/xy-forever/, open "wb", chunk writes, close, read back to EOF with CRC-32, close, op 9.
// mounted must be 0. s_crc when the read-back differs from crc.
uint32_t write_file(const char* path, const uint8_t* data, uint32_t size, uint32_t crc, uint32_t& written,
                    uint32_t& readback_crc, volatile uint32_t& mounted);
// DELETE / RMDIR / MKDIR: op 8 when needed, then the one change (op 0x25, 0x27 or 0x26; A opens
// its read-write window for it, as for FILE_WRITE). mounted must not be stale (s_state; the caller
// sends op 9 first). error: A's error number when it failed (change_status).
uint32_t change(uint32_t op, const char* path, uint32_t& error, volatile uint32_t& mounted);
// RENAME: the pair through valid_rename first (refused: nothing sent), op 8 when needed, then two
// path requests on the path builder 889f8d22 (no new stock call): op 0x21 with argument rename_from and
// the source (A keeps it), then op 0x21 rename_to and the target (A renames in its write window; bound
// change_ms). Before them, a folder moved deeper is walked for the depth rule (#124, file_engine.hpp;
// refused: nothing renamed). mounted as change; error: A's error number (rename_status).
// over (#4): rename_over instead of rename_to: from is an upload (file_engine::upload_size), and an
// existing file at to is replaced in one step (A checks the upload's size; a folder there: EISDIR).
uint32_t rename(const char* from, const char* to, uint32_t& error, volatile uint32_t& mounted, bool over = false);
// Streamed writes (#4, file.write_*), FILE_WRITE's steps: open_write = op 8 when needed, mkdir -p below
// /yaffs2/, open "wb" (fd); write_more = chunk writes (written counts on); close_write; read_crc = open
// read-only, read to EOF (at most size + 3 KiB) with CRC-32, close. mounted as write_file.
uint32_t open_write(const char* path, int& fd, volatile uint32_t& mounted);
uint32_t make_folders(const char* path, volatile uint32_t& mounted);  // open_write's mkdir -p alone (mounted)
uint32_t write_more(int fd, const uint8_t* bytes, uint32_t size, uint32_t& written, volatile uint32_t& mounted);
uint32_t close_write(int fd, volatile uint32_t& mounted);
uint32_t read_crc(const char* path, uint32_t size, uint32_t& total, uint32_t& crc, volatile uint32_t& mounted);
// COPY, the checks before B reads the source: op 8 when needed, then a stat of the target (as
// file.list's; found: s_state, error eexist) and of its folder (missing: s_not_found, error enoent).
// mounted as read_file (stale: op 9 first, in read mode).
uint32_t copy_target(const char* path, uint32_t& error, volatile uint32_t& mounted);
// After a failed copy write: unlink the target (op 0x25, as DELETE) and, when that fails, stat it. 1 when
// a partial target may remain (also when mounted is stale: nothing is sent), else 0.
uint32_t remove_partial(const char* path, volatile uint32_t& mounted);
#endif
}}
#endif
