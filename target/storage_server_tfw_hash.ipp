// A's sequential, keyless hash reader, included by storage_server.cpp.
#if defined(XYF_TFW_CHECK)
namespace th = xy_forever::tfw_hash_rpc;
namespace tp = xyf::usbp;
namespace tfw = xyf_core::tfw;
namespace hash = xyf_core::hash;
struct TfwHashJob {
    int fd;
    U32 status, position, start, end, touched;
    char path[max_path];
    hash::Digest digest;
    U32 sha_storage[(sizeof(hash::Sha256) + 3) / 4];
};
TfwHashJob a_tfw_hash __attribute__((section(".data"))) = {-1, tp::s_state, 0, 0, 0, 0, {}, {}, {}};
hash::Sha256& tfw_hasher() { return *reinterpret_cast<hash::Sha256*>(a_tfw_hash.sha_storage); }
bool tfw_hash_open() { return a_tfw_hash.fd >= 0; }
bool tfw_hash_close(cold_storage::Stock121ReadOnlySession& storage) {
    if (tfw_hash_open() && storage.direct_close(a_tfw_hash.fd)) {
        a_tfw_hash.status = tp::s_storage;
        return false;
    }
    a_tfw_hash.fd = -1;
    return true;
}
bool tfw_hash_clear(cold_storage::Stock121ReadOnlySession& storage) {
    if (!tfw_hash_close(storage)) return false;
    a_tfw_hash.path[0] = 0;
    return true;
}
bool tfw_hash_mode() { return !boot_selected() && !window_open && engine == entry_read && !engine_refused; }
U32 tfw_hash_begin(cold_storage::Stock121ReadOnlySession& storage, const char* path, U32 now) {
    if (a_tfw_hash.path[0] || !tfw_hash_mode() || held_files()) return tp::s_busy;
    if (!mount(storage) || !storage.direct_gate() || !storage.ready()) return tp::s_storage;
    const int fd = storage.direct_open(path);
    if (fd < 0) return tp::s_not_found;
    TfwHashJob& job = a_tfw_hash;
    job.fd = fd;
    job.touched = now;
    for (U32 i = 0; i != max_path; ++i)
        if (!(job.path[i] = path[i])) break;
    uint8_t prefix[tfw::prefix_bytes];
    const int got = storage.direct_read(fd, prefix, sizeof(prefix));
    if (got != int(sizeof(prefix)) || tfw::ciphertext_span(prefix, job.start, job.end) != tfw::Status::ok) {
        return !tfw_hash_clear(storage) || got < 0 ? tp::s_storage : tp::s_bad_request;
    }
    job.position = sizeof(prefix);
    tfw_hasher().reset();
    job.status = tp::s_busy;
    return tp::s_ok;
}
void tfw_hash_stop(cold_storage::Stock121ReadOnlySession& storage, U32 status) {
    a_tfw_hash.status = status;
    tfw_hash_close(storage);
}
void tfw_hash_step(cold_storage::Stock121ReadOnlySession& storage, U32 now) {
    TfwHashJob& job = a_tfw_hash;
    if (!job.path[0]) return;
    // The two direct_ready checks, with ready() called directly for the call audit.
    if (now - job.touched >= th::lease_ms || !tfw_hash_mode() || !storage.direct_gate() || !storage.ready()) {
        tfw_hash_clear(storage);
        return;
    }
    if (job.status != tp::s_busy) return;
    uint8_t bytes[4096];
    if (job.position == job.end) {
        const int got = storage.direct_read(job.fd, bytes, 1);
        if (got) tfw_hash_stop(storage, got < 0 ? tp::s_storage : tp::s_bad_request);
        else tfw_hash_stop(storage, tfw_hasher().finish(job.digest) ? tp::s_ok : tp::s_sha);
        return;
    }
    const bool ciphertext = job.position >= job.start;
    U32 count = (ciphertext ? job.end : job.start) - job.position;
    if (count > sizeof(bytes)) count = sizeof(bytes);
    const int got = storage.direct_read(job.fd, bytes, count);
    if (got != int(count)) tfw_hash_stop(storage, got < 0 ? tp::s_storage : tp::s_bad_request);
    else if (ciphertext && !tfw_hasher().update(bytes, count)) tfw_hash_stop(storage, tp::s_sha);
    else job.position += count;
}
__attribute__((noinline)) bool tfw_hash_query(void* context, U32 request, Stock121PeerSession& session) {
    const U32 argument = word(request + 8), op = argument & 0xffu;
    if (word(request) != path_class || byte(request + 5) != 0x21u ||
        (argument & th::tag_mask) != (th::tag & th::tag_mask))
        return false;
    rename_armed = 0;                                  // breaks the adjacency of rename requests
    if (argument >> 24 != th::version) {
        session.reply(request, tp::s_unsupported, th::version);
        return true;
    }
    if (word(request + 0x18) != path_buffer) {
        session.reply(request, tp::s_bad_request, 0);
        return true;
    }
    for (U32 i = 0; i != max_path; ++i)
        if (!(path_copy[i] = char(byte(path_buffer + i)))) break;
    if (file_engine::decide_engine(file_engine::op_read, path_copy) != file_engine::allow ||
        !services::loader_path(path_copy)) {
        session.reply(request, tp::s_bad_request, 0);
        return true;
    }
    latch(session);
    const U32 now = xyf::clock::ms();
    auto& storage = *static_cast<cold_storage::Stock121ReadOnlySession*>(context);
    TfwHashJob& job = a_tfw_hash;
    U32 code = tp::s_state, value = 0;
    if (job.path[0] && (now - job.touched >= th::lease_ms || !tfw_hash_mode())) tfw_hash_clear(storage);
    if (op == th::start) {
        if ((session.last_command() >> 8 & 0xffu) == install_item) code = tp::s_busy;
        else code = tfw_hash_begin(storage, path_copy, now);
        if (code == tp::s_ok) value = job.end;
    } else if (job.path[0]) {
        U32 i = 0;
        while (path_copy[i] && path_copy[i] == job.path[i]) ++i;
        if (path_copy[i] == job.path[i]) {
            if (op == th::cancel) code = tfw_hash_clear(storage) ? tp::s_ok : tp::s_storage;
            else if (op == th::status) {
                job.touched = now;
                code = job.status;
                if (code == tp::s_busy || code == tp::s_ok) value = job.position;
            } else if (op >= th::digest && op < th::digest + th::digest_words && job.status == tp::s_ok) {
                job.touched = now;
                code = tp::s_ok;
                value = tp::get32(job.digest.bytes + 4 * (op - th::digest));
            }
        }
    }
    session.reply(request, code, value);
    return true;
}
#else
bool tfw_hash_open() { return false; }
bool tfw_hash_query(void*, U32, Stock121PeerSession&) { return false; }
#endif
