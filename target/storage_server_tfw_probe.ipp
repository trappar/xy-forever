// A's bounded read-only cut reader. Only admitted direct_open/read/close, no seek entry.
#if defined(XYF_TFW_CHECK)
uint8_t tfw_reply[40] __attribute__((section(".data"))) = {};
bool tfw_reply_ready __attribute__((section(".data"))) = false;
struct ACheckFile {
    cold_storage::Stock121ReadOnlySession& storage;
    const char* path;
    int fd;
    U32 position, base, bytes;
    uint8_t cache[1024];
};
int a_check_read(void* context, U32 offset, uint8_t* out, U32 count) {
    ACheckFile& f = *static_cast<ACheckFile*>(context);
    if (offset > tfw_role_probe::max_cut || count > tfw_role_probe::max_cut - offset) return -1;
    U32 done = 0;
    while (done != count) {
        const U32 at = offset + done;
        if (at < f.base || at - f.base >= f.bytes) {
            if (at < f.position) {
                if (f.storage.direct_close(f.fd)) { f.fd = -1; return -1; }
                f.fd = f.storage.direct_open(f.path); f.position = 0; f.bytes = 0;
                if (f.fd < 0) return -1;
            }
            do {
                // Retain CBC's preceding block when a small read straddles cache windows.
                const U32 keep = f.bytes < 64 ? f.bytes : 64;
                for (U32 i = 0; i != keep; ++i) f.cache[i] = f.cache[f.bytes - keep + i];
                f.base = f.position - keep;
                const int got = f.storage.direct_read(f.fd, f.cache + keep, sizeof(f.cache) - keep);
                if (got < 0 || got > int(sizeof(f.cache) - keep)) return -1;
                f.bytes = keep + U32(got); f.position += U32(got);
                if (!got) return int(done);
            } while (at >= f.position);
        }
        const U32 available = f.bytes - (at - f.base);
        const U32 n = count - done < available ? count - done : available;
        for (U32 i = 0; i != n; ++i) out[done + i] = f.cache[at - f.base + i];
        done += n;
    }
    return int(done);
}
bool tfw_probe_query(void* context, U32 request, Stock121PeerSession& session) {
    const U32 argument = word(request + 8), op = argument & 0xffu;
    if (word(request) != path_class || byte(request + 5) != 0x21u ||
        (argument & tfw_role_probe::tag_mask) != (tfw_role_probe::tag & tfw_role_probe::tag_mask)) return false;
    cold_storage::Stock121ReadOnlySession& storage = *static_cast<cold_storage::Stock121ReadOnlySession*>(context);
    if ((argument >> 24) != tfw_role_probe::version) {
        tfw_reply_ready = false; session.reply(request, xyf::usbp::s_unsupported, tfw_role_probe::version); return true;
    }
    if (!op) { tfw_reply_ready = false; session.reply(request, tfw_role_probe::version, 0); return true; }
    if (probe_mode || boot_selected() || window_now() || held_files() || engine != entry_read) {
        tfw_reply_ready = false; session.reply(request, xyf::usbp::s_busy, 0); return true;
    }
    if (op == 1) {
        tfw_reply_ready = false;
        const char* path = reinterpret_cast<const char*>(path_buffer);
        U32 code = xyf::usbp::s_bad_request;
        if (storage_server::valid_path(path)) {
            code = xyf::usbp::s_storage;
            if (mount(storage)) {
                ACheckFile f = {storage, path, -1, 0, 0, 0, {0}};
                f.fd = storage.direct_open(path);
                if (f.fd < 0) code = xyf::usbp::s_not_found;
                else {
                    const xyf_core::tfw_check::File file = {&f, a_check_read};
                    code = tfw_device_check::run(path, tfw_reply, file);
                    if (f.fd >= 0 && storage.direct_close(f.fd)) code = xyf::usbp::s_storage;
                    tfw_reply_ready = code == xyf::usbp::s_ok;
                }
            }
        }
        session.reply(request, code, tfw_role_probe::version);
    } else if (op >= 2 && op < 7 && tfw_reply_ready) {
        const U32 at = (op - 2) * 8;
        session.reply(request, xyf::usbp::get32(tfw_reply + at), xyf::usbp::get32(tfw_reply + at + 4));
        if (op == 6) tfw_reply_ready = false;
    } else session.reply(request, xyf::usbp::s_state, 0);
    return true;
}
#else
bool tfw_probe_query(void*, U32, Stock121PeerSession&) { return false; }
#endif
