// Included by usb_service.cpp: existing bounded storage IO shared by its jobs.
struct ContentIo {
    U32 link;
    int fail(const file_client::Reply& r) {
        if (r.outcome == file_client::link) mounted = file_client::mounted_stale;
        if (r.outcome != file_client::refused) link = 1;                // nothing more is sent
        return -1;
    }
    static bool writable(const char* p) { return storage_server::own_area(p) && storage_server::valid_write_path(p); }
    int open(const char* p, bool write) {
        if (link || !storage_server::valid_path(p) || (write && !writable(p))) return -1;
        const file_client::Reply r = file_client::open(p, write ? storage_server::write_open_flags : 0u);
        if (r.outcome) return fail(r);
        return r.value >= 0 ? r.value : !write && r.error == file_client::enoent ? -2 : -1;
    }
    int read(int fd, uint8_t* out, U32 n) {
        if (link || fd < 0 || !n || n > file_client::chunk) return -1;
        const file_client::Reply r = file_client::read(fd, out, n);
        if (r.outcome) return fail(r);
        return r.value < 0 || U32(r.value) > n ? -1 : r.value;
    }
    int write(int fd, const uint8_t* data, U32 n) {
        if (link || fd < 0 || !n || n > file_client::chunk) return -1;
        const file_client::Reply r = file_client::write(fd, data, n);
        if (r.outcome) return fail(r);
        return r.value < 0 || U32(r.value) > n ? -1 : r.value;
    }
    int close(int fd) {
        if (link || fd < 0) return -1;
        const file_client::Reply r = file_client::close(fd);
        if (r.outcome) return fail(r);
        return r.value ? -1 : 0;
    }
    int unlink(const char* p) {
        if (link || !writable(p)) return -1;
        const file_client::Reply r = file_client::unlink(p);
        if (r.outcome) return fail(r);
        return r.value >= 0 ? 0 : r.error == file_client::enoent ? 1 : -1;
    }
    int rmdir(const char* p) {
        if (link || !writable(p)) return -1;
        const file_client::Reply r = file_client::rmdir(p);
        if (r.outcome) return fail(r);
        return r.value >= 0 || r.error == file_client::enoent ? 0 : r.error == file_client::enotempty ? 1 : -1;
    }
    int mkdir(const char* p) {
        if (link || !writable(p)) return -1;
        const file_client::Reply r = file_client::mkdir(p);
        if (r.outcome) return fail(r);
        if (r.value >= 0) return 0;
        if (r.error != file_client::eexist) return -1;
        uint8_t record[file_client::record_bytes];                       // there already: a folder?
        const file_client::Reply q = file_client::stat(p, record);
        if (q.outcome) return fail(q);
        return q.value == 0 && (P::get32(record + 8) & 0xf000u) == 0x4000u ? 0 : -1;
    }
    int rename(const char* from, const char* to, bool over = false) {
        if (link) return -1;
        uint32_t error = 0;
        const U32 code = file_client::rename(from, to, error, mounted, over);
        if (code == P::s_link || code == P::s_state) link = 1;
        return code == P::s_ok ? 0 : -1;
    }
#if defined(XYF_CONTENT_SWITCH)
    // File metadata from the existing stat RPC. Return 1 for an absent name.
    int stat_asset(const char* path, xyf::managed_asset::File& file) {
        if (link || !storage_server::valid_path(path)) return -1;
        uint8_t record[file_client::record_bytes];
        const file_client::Reply r = file_client::stat(path, record);
        if (r.outcome) return fail(r);
        if (r.value) return r.error == file_client::enoent ? 1 : -1;
        if ((P::get32(record + 8) & 0xf000u) != 0x8000u) return -1;
        file.device = P::get32(record);
        file.inode = P::get32(record + 4);
        file.size = P::get32(record + 28);
        return 0;
    }
    int stage_blob_link(const char* temporary, const uint8_t* hash, bool seal = false) {
        if (link || !writable(temporary)) return -1;
        const file_client::Reply r = file_client::stage_blob_link(temporary, hash, seal);
        if (r.outcome) return fail(r);
        if (r.value == P::s_not_found) return 1;
        if (r.value == P::s_state) return 2;
        return r.value == P::s_ok ? 0 : -1;
    }
    int copy_blob_piece() {
        const file_client::Reply r = file_client::copy_blob_piece();
        if (r.outcome) return fail(r);
        return r.value == P::s_ok && r.error <= 1 ? int(r.error) : -1;
    }
    int publish_blob_link(const char* from, const char* to) {
        return writable(from) && writable(to) ? rename(from, to, true) : -1;
    }
#endif
    int list_one(const char* folder, U32 skip, char* name, uint32_t& kind) {
        if (link || file_engine::decide_engine(file_engine::op_list, folder) != file_engine::allow) return -1;
        uint8_t page[6 + file_client::max_name];
        uint32_t count = 0, more = 0, bytes = 0;
        const U32 code = file_client::list(folder, skip, page, sizeof(page), count, more, bytes, mounted);
        if (code == P::s_not_found) return 0;
        if (code != P::s_ok) {
            if (code == P::s_link) link = 1;
            return -1;
        }
        if (!count) return 0;
        kind = page[0];
        const U32 n = page[5];
        for (U32 i = 0; i != n; ++i) name[i] = char(page[6 + i]);
        name[n] = 0;
        return 1;
    }
    bool lost() const { return link != 0; }
};

// The idle collector normally reads only; open the existing window just for a
// proven-unreferenced blob's unlink, as tree_change does for scratch cleanup.
bool journal_kept();
struct GarbageIo : ContentIo {
    int unlink(const char* p) {
        if (link || journal_kept()) return -1;
        const U32 code = enter_writes();
        if (code != P::s_ok) {
            if (code == P::s_link) link = 1;
            return -1;
        }
        storage_used = 1;
        writes_due = 1;
        writes_ms = now();
        return ContentIo::unlink(p);
    }
};
