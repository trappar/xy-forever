#ifndef XYF_MTP_RESPONDER_HPP
#define XYF_MTP_RESPONDER_HPP
#include "ldr.hpp"
// The MTP (PTP) responder core, freestanding (no heap, no libc), no USB. The transport hands it
// whole bulk-OUT transfers (receive), takes the bulk-IN byte stream (take: data container, then the
// response container; one container per IN phase, so the transport ends a phase whose length is a
// multiple of the packet size with a ZLP), forwards the class request Cancel (cancel) and asks the
// device status (device_status). All storage work happens in step(), at most one file call per step,
// so B's main loop can run it between USB services (docs/decisions/002-usb-function-driver.md).
//
// One storage (storage_id) rooted at /yaffs2. Changes (SendObjectInfo, SendObject, DeleteObject, renames)
// anywhere below /yaffs2/ (the write rule, since 1.13) except /yaffs2/bootloader.dev
// (the dev-menu marker) and /yaffs2/upgrade and below (the stock updater's inbox), both ASCII case ignored:
// those are read-only (ProtectionStatus 1): creating answers Access_Denied, deleting Object_WriteProtected.
// The engine's policy (B, then A, which also follows links) refuses the same.
//
// Object handles are assigned when a folder is listed (or an object created), from a bounded table
// (max_handles, names in a name_pool); the same path keeps its handle for the session. OpenSession
// and CloseSession clear the table; a deleted object's slot is reused, its name bytes only then.
// Limits: GetObjectHandles with parent 0 lists the root only (no recursive "all objects"); DeleteObject
// of a folder removes only an empty folder (else Partial_Deletion); file sizes are 32-bit.
//
// T2c: MTP object properties (go-mtpfs / go-mtpx read ObjectSize through GetObjectPropValue; renames are
// SetObjectPropValue ObjectFileName): GetObjectPropsSupported, GetObjectPropDesc, GetObjectPropValue (from
// the handle table, no file call), SetObjectPropValue (ObjectFileName only: a rename where writable() allows
// both names; A refuses an existing new name: Invalid_ObjectProp_Value; any other property Access_Denied), GetObjectPropList for one object at depth
// 0 (answered, not listed in DeviceInfo: a client that sees it asks for whole trees, which is not answered).
// Properties: object_props below. DateModified is a fixed date (the file client has no times yet).
namespace xyf_core { namespace mtp {
typedef unsigned short U16;
// FileOps results: >= 0 done (open: a handle; read/write: bytes), else one of these.
enum FileError : int { f_not_found = -2, f_storage = -5, f_refused = -13, f_exists = -17,
                       f_not_empty = -93, f_link = -110 };
static const U32 max_name = 254;                         // UTF-8 bytes of one name
struct Entry { U32 kind; U32 size; char name[max_name + 2]; };   // kind 0 file, 1 folder, 2 other
// The storage calls, shaped like processor B's file client (target/file_client.hpp): one page of a
// folder (entries after skip, each with its stat; more = 1 when the folder goes on), stat, statfs,
// open/read/write/seek/close (read, write <= chunk bytes), mkdir, unlink, rmdir, rename.
struct FileOps {
    void* context;
    int (*list)(void* context, const char* folder, U32 skip, Entry* out, U32 room, U32& count, U32& more);
    int (*stat)(void* context, const char* path, Entry& out);                 // kind and size
    int (*space)(void* context, const char* path, U32& block, U32& blocks, U32& free_blocks);
    int (*open)(void* context, const char* path, bool write);                 // write: create/truncate
    int (*read)(void* context, int fd, Byte* out, U32 count);
    int (*write)(void* context, int fd, const Byte* data, U32 count);
    int (*seek)(void* context, int fd, U32 offset);
    int (*close)(void* context, int fd);
    int (*mkdir)(void* context, const char* path);
    int (*unlink)(void* context, const char* path);
    int (*rmdir)(void* context, const char* path);
    int (*rename)(void* context, const char* from, const char* to);
    // Hooks (optional, null: none): prepare runs before an operation that reaches the files; nonzero is the
    // response instead (Device_Busy while another user holds B's file client). log sees every operation:
    // code, first parameter, response code, data: 1 a data phase came in, 2 one went out, 3 an unasked data
    // phase was dropped (code = its container code).
    // T2c: log gets the operation's parameters (params[0..2]; nparams of them came with the command).
    U32 (*prepare)(void* context);
    void (*log)(void* context, U32 code, const U32* params, U32 nparams, U32 response, U32 data);
};
static const U32 chunk = 1024;                           // bytes per read or write call (file_client::chunk)
static const U32 buffer_bytes = 4096;                    // largest OUT transfer; IN staging
static const U32 max_handles = 512, name_pool = 16384, page = 16;   // page: entries per list call
static const U32 max_path = 240;                         // with the NUL (file_engine::max_path)
static const U32 storage_id = 0x00010001u;
// Container types, operation and response codes (PTP 1.1 / MTP 1.1).
enum : U16 { t_command = 1, t_data = 2, t_response = 3 };
enum : U16 {
    op_get_device_info = 0x1001, op_open_session = 0x1002, op_close_session = 0x1003,
    op_get_storage_ids = 0x1004, op_get_storage_info = 0x1005, op_get_object_handles = 0x1007,
    op_get_object_info = 0x1008, op_get_object = 0x1009, op_delete_object = 0x100b,
    op_send_object_info = 0x100c, op_send_object = 0x100d, op_get_device_prop_desc = 0x1014,
    op_get_device_prop_value = 0x1015, op_get_partial_object = 0x101b,
    op_get_object_props_supported = 0x9801, op_get_object_prop_desc = 0x9802, op_get_object_prop_value = 0x9803,
    op_set_object_prop_value = 0x9804, op_get_object_prop_list = 0x9805
};
// T2c: object property codes and data types.
enum : U16 {
    p_storage_id = 0xdc01, p_object_format = 0xdc02, p_protection_status = 0xdc03, p_object_size = 0xdc04,
    p_object_file_name = 0xdc07, p_date_modified = 0xdc09, p_parent_object = 0xdc0b, p_puoid = 0xdc41, p_name = 0xdc44
};
enum : U16 { d_u16 = 0x0004, d_u32 = 0x0006, d_u64 = 0x0008, d_u128 = 0x000a, d_string = 0xffff };
static const char fixed_date[] = "20250101T000000";      // DateModified, ObjectInfo's dates
static const U32 prop_friendly_name = 0xd402;            // the only device property (a string, read-only)
enum : U16 {
    r_ok = 0x2001, r_general_error = 0x2002, r_session_not_open = 0x2003, r_not_supported = 0x2005,
    r_incomplete_transfer = 0x2007, r_invalid_storage_id = 0x2008, r_invalid_object_handle = 0x2009,
    r_store_full = 0x200c, r_object_write_protected = 0x200d, r_access_denied = 0x200f,
    r_partial_deletion = 0x2012, r_no_valid_object_info = 0x2015, r_device_busy = 0x2019,
    r_device_prop_not_supported = 0x200a,
    r_invalid_parent = 0x201a, r_invalid_parameter = 0x201d, r_session_already_open = 0x201e,
    r_invalid_dataset = 0x2023,
    r_invalid_object_prop_code = 0xa801, r_invalid_object_prop_value = 0xa803,
    r_specification_by_group_unsupported = 0xa807, r_specification_by_depth_unsupported = 0xa808
};
enum : U16 { f_undefined = 0x3000, f_association = 0x3001 };
// The write rule (pure): below /yaffs2/, first name not "upgrade", not the marker /yaffs2/bootloader.dev
// (ASCII case ignored).
bool writable(const char* path);

class Responder {
public:
    void init(const FileOps& ops);                       // also a device reset: no session
    // One bulk-OUT transfer (<= buffer_bytes). false: not now (the transport keeps it and calls
    // step() first; this is the flow control of decision 002: the urb stays held).
    bool receive(const Byte* data, U32 count);
    U32 pending() const { return cancelling ? 0 : out_len - out_pos; }
    U32 take(Byte* out, U32 room);                       // bulk-IN bytes
    // One bounded piece of work (at most one file call). false: nothing to do until the host acts.
    bool step();
    void cancel() { cancelling = 1; }                    // class request Cancel (may come from an interrupt)
    void reset() { resetting = 1; cancelling = 1; }      // Device Reset, bus reset: cancel, then no session
    // An operation is in flight (it may hold a file): other users of the file client must wait.
    bool busy() const { return cancelling || phase != p_idle || fd >= 0; }
    U32 staged_length() const;                           // the length of the container at the IN head (0: < 4 bytes)
    bool session_open() const { return session != 0; }
    U16 device_status() const { return cancelling || phase == p_close ? r_device_busy : r_ok; }
    U32 handles_used() const;
    // Speed (2026-11-15): GetObjectInfo of an object whose kind and size came from the last listing of its folder
    // (or from our own change) is answered from the handle table at once: no file call, no prepare. invalidate()
    // forgets every such fact (the next GetObjectInfo stats); the session calls it on every change made outside
    // MTP (serial file commands, jobs). A listing marks its folder's children stale first, then fresh as listed.
    void invalidate();
    U32 cache_hits() const { return hits; }
private:
    enum Phase : U32 { p_idle, p_data, p_work, p_close };
    struct Object { U16 parent; U16 name_at; Byte name_len, kind, used, fresh; U32 size; };   // fresh: kind, size known
    FileOps ops;
    volatile U32 cancelling, resetting;
    U32 data_flag;
    U32 phase, stage, session, tid, nparams, params[5];
    U16 code;
    // OUT data phase
    U32 data_total, data_got, data_seen, in_len, in_pos;
    Byte in[buffer_bytes];
    // IN staging
    U32 out_len, out_pos;
    Byte out[buffer_bytes + 64];
    // the operation in progress
    int fd;
    U32 left, failed, result, target, skip, more, count, offset, opened;
    U16 listed[max_handles];
    Entry entries[page];
    char path[max_path];
    U32 pending_handle, pending_size;                    // SendObjectInfo -> SendObject
    Object objects[max_handles];
    char names[name_pool];
    U32 names_used;
    U32 hits;
    bool cached_info();                                  // GetObjectInfo from the table (true: answered)
    void object_info(U32 handle, const char* where);     // the ObjectInfo dataset and the response

    void clear_objects();
    void respond(U16 code, U32 n = 0, U32 a = 0, U32 b = 0, U32 c = 0);
    void command(const Byte* data, U32 count);
    void start();
    bool work();
    bool path_of(U32 handle, char* out) const;
    U32 find(U32 parent, const char* name) const;
    U32 add(U32 parent, const char* name, U32 kind, U32 size);
    void forget(U32 handle);                             // a deleted object (and what was below it)
    bool valid(U32 handle) const { return handle && handle <= max_handles && objects[handle - 1].used; }
    bool parent_of(U32 param, U32& parent) const;
    Byte* begin_data(U32 payload);                       // reserves header + payload in out
    void end_data(U32 payload);
    bool stream();                                       // GetObject / GetPartialObject reads
    U32 prop_value(Byte* p, U32 handle, U32 prop);       // T2c: one property's value at p: bytes (0: unknown)
    bool rename_object(U32 handle, const char* name);    // the table after a rename (false: forgotten)
    U32 error_code(int error) const;
};
} }
#endif
