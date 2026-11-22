// Included by storage_server.cpp after the normal mount/marker helpers: #202's managed content links.
#if defined(XYF_CONTENT_SWITCH)
namespace HP = xyf::usbp;
constexpr U32 yaffs_device_at = 0x08, yaffs_mode_at = 0x34;
constexpr U32 yaffs_file = 1, yaffs_hardlink = 4;
#if defined(XYF_SIM)
// The simulator reserves a separate pool for its fake lookup objects.
constexpr U32 object_low = 0x8aaa0000u, object_high = 0x8ab00000u;
#else
constexpr U32 object_low = cold_heap::arena_base, object_high = cold_heap::arena_end;
#endif

// Raw lookup of one fixed component. Do not follow an alias in the parent
// walk or accept a name whose reported parent differs from its real parent.
__attribute__((noinline)) bool hardlink_lookup(U32 dir, const char* name, U32 device, U32& result) {
    int notdir = 0, loop = 0;
    U32 parent = 0;
    result = stock<0x889fa2beu, U32>(dir, name, 0, 0, &parent, &notdir, &loop);
    if (notdir || loop || (dir && parent != dir)) return false;
    if (!result) return dir != 0;
    return !(result & 3u) && result >= object_low && result <= object_high - 0x70u &&
           word(result + yaffs_device_at) == device && (!dir || word(result + object_parent) == dir);
}

__attribute__((noinline, noclone)) U32 hardlink_create_alias(U32 dir, const char* name, U32 source) {
    // Stock YAFFS generic object creator, type 4. The two object pointers
    // were obtained on this same mount. It returns the new alias or null.
    return stock<0x88a0199au, U32>(yaffs_hardlink, dir, name, 0u, 0u, 0u, source, static_cast<const char*>(0), 0u);
}

namespace ml = managed_blob_link;

bool managed_directory(U32 parent, const char* name, U32 device, U32& child) {
    return hardlink_lookup(parent, name, device, child) && child && word(child + object_type) == type_folder;
}

struct ManagedObjects {
    U32 device, tmp, source;
};

U32 managed_objects(const char* name, const char* hash, ManagedObjects& found) {
    uint32_t retained = 0;
    if (retained_lookup::stock121_yaffs_device(retained) != retained_lookup::Result::ok) return HP::s_storage;
    found.device = retained;
    if (word(found.device + 0xe4u) != 1 || word(found.device + 0xe8u) != 0) return HP::s_storage;
    U32 mount_dir = 0, xyf_dir = 0, blobs = 0, alias = 0;
    if (!managed_directory(0, "/yaffs2", found.device, mount_dir) ||
        !managed_directory(mount_dir, "xy-forever", found.device, xyf_dir) ||
        !managed_directory(xyf_dir, "tmp", found.device, found.tmp) ||
        !hardlink_lookup(xyf_dir, "blobs", found.device, blobs))
        return HP::s_refused;
    if (!blobs) return HP::s_not_found;
    if (word(blobs + object_type) != type_folder || !hardlink_lookup(blobs, hash, found.device, found.source))
        return HP::s_refused;
    if (!found.source) return HP::s_not_found;
    if (word(found.source + object_type) != yaffs_file || !hardlink_lookup(found.tmp, name, found.device, alias) ||
        alias)
        return HP::s_refused;
    return HP::s_ok;
}

U32 stage_managed_link(const char* name, const ManagedObjects& found) {
    if ((word(found.source + yaffs_mode_at) & 0777u) != 0444u) return HP::s_state;
    const U32 created = hardlink_create_alias(found.tmp, name, found.source);
    U32 alias = 0;
    if (!created || !hardlink_lookup(found.tmp, name, found.device, alias) || alias != created ||
        word(alias + object_type) != yaffs_hardlink || word(alias + object_alias) != found.source)
        return HP::s_storage;
    return HP::s_ok;
}

U32 open_managed_seal(cold_storage::Stock121ReadOnlySession& storage, const char* temp, const char* hash,
                      const ManagedObjects& found) {
    if ((word(found.source + yaffs_mode_at) & 0777u) == 0444u) return HP::s_state;
    uint32_t count = 0;
    if (!file_engine::upload_size(temp, count)) return HP::s_bad_request;
    char source[max_path];
    ml::blob_path(source, hash);
    managed_blob_in = storage.managed_blob_open(source, false);
    if (managed_blob_in < 0) return HP::s_storage;
    managed_blob_out = storage.managed_blob_open(temp, true);
    if (managed_blob_out < 0) {
        close_managed_blob(storage);
        return HP::s_storage;
    }
    managed_blob_left = count;
    return HP::s_ok;
}

U32 copy_managed_seal(cold_storage::Stock121ReadOnlySession& storage, U32& detail) {
    const U32 count = managed_blob_left < ml::piece_bytes ? managed_blob_left : ml::piece_bytes;
    const auto result = storage.managed_blob_copy(managed_blob_in, managed_blob_out, count);
    if (result != cold_storage::Result::ok) {
        close_managed_blob(storage);
        return HP::s_storage;
    }
    if (count) managed_blob_left -= count;
    else if (!close_managed_blob(storage)) return HP::s_storage;
    else detail = 1;                                              // closed and length matched
    return HP::s_ok;
}

// #195: create's file (a sealed file B writes itself): open without a source.
bool managed_writing() { return managed_blob_out >= 0 && managed_blob_in < 0; }

U32 create_managed(cold_storage::Stock121ReadOnlySession& storage) {
    if (!write_root(path_copy) || file_engine::decide_engine(file_engine::op_write, path_copy) != file_engine::allow ||
        reaches_protected(path_copy))
        return HP::s_refused;
    managed_blob_out = storage.managed_blob_create(path_copy);
    return managed_blob_out >= 0 ? HP::s_ok : HP::s_storage;
}

// put: B's piece is the write request's body (+0x20, its bytes +0x24), as the stock write executor reads it (and A's
// staging, storage_server_a_stage.ipp: the stock receiver stores the count at +0x18 too).
U32 put_managed(cold_storage::Stock121ReadOnlySession& storage, U32 request, U32& detail) {
    const U32 bytes = word(request + 0x24);
    if (!bytes || bytes > ml::piece_bytes || word(request + 0x18) != bytes || !word(request + 0x20)) {
        close_managed_blob(storage);
        return HP::s_bad_request;
    }
    if (storage.managed_blob_write(managed_blob_out, reinterpret_cast<const void*>(word(request + 0x20)), bytes) !=
        cold_storage::Result::ok) {
        close_managed_blob(storage);
        return HP::s_storage;
    }
    detail = bytes;
    return HP::s_ok;
}

bool managed_link_query(void* context, U32 request, Stock121PeerSession& session) {
    const U32 arg = word(request + 8), op = arg & 0xffu;
    const bool put = word(request) == file_engine::write_class && byte(request + 5) == 0x2bu;
    if ((!put && (word(request) != path_class || byte(request + 5) != 0x21u)) || (arg & ml::mask) != ml::tag)
        return false;
    rename_armed = 0;
    const char* hash = 0;
    U32 code = HP::s_ok, detail = 0;
    if (arg >> 24 != ml::version) code = HP::s_unsupported;
    else if (put ? op != ml::put : op > ml::create || word(request + 0x18) != path_buffer) code = HP::s_bad_request;
    else if (op == ml::create) {
        U32 n = 0;
        while (n != max_path && (path_copy[n] = char(byte(path_buffer + n)))) ++n;
        if (n == max_path || !ml::scratch_path(path_copy)) code = HP::s_bad_request;
    } else if (!put && op != ml::copy) {
        for (U32 i = 0; i != ml::frame_bytes; ++i) path_copy[i] = char(byte(path_buffer + i));
        if (!ml::decode(path_copy, hash, op == ml::seal)) code = HP::s_bad_request;
    }
    if (!code) {
        latch(session);
        // B's own files (its source) may stay open while it writes create's file; the links and seals need none.
        const bool writing = managed_writing(), own = op == ml::create || op == ml::put || (op == ml::copy && writing);
        const bool state = op == ml::put ? writing : (op == ml::copy) == managed_blob_active();
        if (probe_mode || boot_selected() || (!own && held_files()) || !window_open || engine != entry_write ||
            !state || window_result != U32(cold_storage::Result::ok))
            code = HP::s_busy;
        else if (engine_refused || !spans_match()) code = HP::s_identity;
        else {
            for (const auto& span : ml::creator_spans)
                if (crc(span[0], span[1]) != span[2]) {
                    code = HP::s_identity;
                    break;
                }
            if (!code) {
                auto& storage = *static_cast<cold_storage::Stock121ReadOnlySession*>(context);
                window_touched = 1;
                if (op == ml::put) code = put_managed(storage, request, detail);
                else if (op == ml::create) code = create_managed(storage);
                else if (op == ml::copy && writing) {
                    if (!close_managed_blob(storage)) code = HP::s_storage;
                    else detail = 1;                                      // closed: B's hash names it
                } else if (op == ml::copy) code = copy_managed_seal(storage, detail);
                else {
                    const char* name = path_copy + sizeof(ml::scratch) - 1;
                    ManagedObjects found = {};
                    code = managed_objects(name, hash, found);
                    if (!code)
                        code = op == ml::stage ? stage_managed_link(name, found) :
                                                 open_managed_seal(storage, path_copy, hash, found);
                }
            }
        }
    }
    session.reply(request, code, detail);
    return true;
}

#endif
