// Part of usb_service.cpp, included there at this point: not compiled alone.
// FILE_READ_STREAM (protocol 1.33): path NUL + u32 offset + u32 length (1..stream_piece()). Reply: u32 status,
// then up to length bytes (fewer only at the end of the file). As a ranged file.read, but B keeps the file open on A
// after the reply: the next request for the same path at the offset where this one ended reads on with one storage
// read per piece (file.read: A's check, open, seek, read, probe and close each time). The requests are file.read's
// (file_client: the same path policy, mount, open, seek, read and close; no new stock call). The file is closed right
// after the reply whose reads reached its end (rs_done then answers the requests a tool sent past it, 0 bytes, with
// no request to A); else (after finish_read's 1-byte probe: Test 041) on any other request (home_receive, before it
// runs: read_stream_forget), after read_stream_idle_ms without one (read_stream_turn), or on a request that does not
// continue it. While it is open, the menu loop's own file work waits as for an MTP operation (XYF_OR_MTP_BUSY). A's
// handle is used only while no op 8 or op 9 went out since the open (file_client::mount_epoch: op 9 closes B's files
// on A, and a later open could get the same number); past a request's bound the mount is stale and the handle is
// forgotten, as file.read's.
static const U32 read_stream_idle_ms = 1000, read_stream_check_ms = 1000;
volatile U32 rs_open __attribute__((section(".data"))) = 0;
int rs_fd __attribute__((section(".data"))) = -1;
U32 rs_at __attribute__((section(".data"))) = 0;      // the offset the next piece starts at
U32 rs_epoch __attribute__((section(".data"))) = 0;   // file_client::mount_epoch() at the open
U32 rs_ms __attribute__((section(".data"))) = 0;      // the last request
U32 rs_checked_ms __attribute__((section(".data"))) = 0; // A last shown serving (a_serving)
U32 rs_eof __attribute__((section(".data"))) = 0;     // a read reached the end of the file
U32 rs_done __attribute__((section(".data"))) = 0;    // closed at the end of rs_path, which is rs_at bytes long
char rs_path[file_engine::max_path] __attribute__((section(".data"))) = {0};

bool read_stream_held() { return rs_open != 0; }
bool rs_handle_valid() { return mounted == 1 && file_client::mount_epoch() == rs_epoch; }
void read_stream_end() {
    if (!rs_open) return;
    rs_open = 0;
    if (rs_handle_valid()) file_client::finish_read(file_client::Open{rs_fd, rs_eof == 0}, mounted);
}
void read_stream_forget() {
    read_stream_end();
    rs_done = 0;
}
void read_stream_turn() {
    if (rs_open && now() - rs_ms > read_stream_idle_ms) read_stream_end();
}
// A file.read-style status for a failed request: past its bound the mount is stale (A may still answer it).
U32 rs_failed(const file_client::Reply& r) {
    if (r.outcome == file_client::link) mounted = file_client::mounted_stale;
    rs_open = 0;
    return file_client::status(r.outcome);
}
bool rs_same(const char* path) {
    for (U32 i = 0;; ++i) {
        if (path[i] != rs_path[i]) return false;
        if (!path[i]) return true;
    }
}
// Opens path at offset for the stream (s_ok), else the status; the stream stays closed on any failure.
U32 rs_open_at(const char* path, U32 offset) {
    namespace fc = file_client;
    if (!storage_server::valid_path(path)) return P::s_bad_request;
    if (file_engine::decide_engine(file_engine::op_read, path) != file_engine::allow) return P::s_refused;
    if (!a_serving()) return P::s_link;
    const U32 ready = fc::mount_ready(mounted);
    if (ready != P::s_ok) return ready;
    const fc::Reply o = fc::open(path, 0);                                // op 0x29, O_RDONLY
    if (o.outcome) return rs_failed(o);
    if (o.value < 0) return o.error == fc::emfile ? P::s_storage : P::s_not_found;
    rs_fd = o.value;
    rs_epoch = fc::mount_epoch();
    rs_eof = 0;
    rs_open = 1;
    if (offset) {
        const fc::Reply k = fc::seek(rs_fd, int(offset));                  // op 0x2c SEEK_SET
        if (k.outcome) return rs_failed(k);
        if (k.value != int(offset)) {
            read_stream_end();
            return P::s_range;
        }
    }
    U32 i = 0;
    for (; path[i]; ++i) rs_path[i] = path[i];
    rs_path[i] = 0;
    rs_at = offset;
    rs_checked_ms = now();
    return P::s_ok;
}
void read_stream(uint8_t seq, const uint8_t* payload, U32 length) {
    namespace fc = file_client;
    U32 path_bytes = 0;
    while (path_bytes != length && payload[path_bytes]) ++path_bytes;
    const char* path = reinterpret_cast<const char*>(payload);
    const U32 offset = path_bytes + 9 == length ? P::get32(payload + path_bytes + 1) : 0;
    const U32 want = path_bytes + 9 == length ? P::get32(payload + path_bytes + 5) : 0;
    U32 code = P::s_ok, bytes = 0;
    if (path_bytes + 9 != length || path_bytes + 1 > storage_server::max_path || !want || want > stream_piece())
        code = P::s_bad_request;
    else if (rs_done && rs_same(path) && offset >= rs_at) {
        status_only(P::t_read_stream, seq, P::s_ok);                     // past the end it just read: nothing
        return;
    } else if (rs_open && rs_same(path) && rs_at == offset && rs_handle_valid()) {
        // Speed (as MTP's mtp_serving_ms): A's check at most once per read_stream_check_ms while the file stays open.
        if (now() - rs_checked_ms >= read_stream_check_ms) {
            if (a_serving()) rs_checked_ms = now();
            else {
                rs_open = 0;                                               // A is not ours: nothing sent to it
                code = P::s_link;
            }
        }
    } else {
        read_stream_forget();
        code = rs_open_at(path, offset);
    }
    while (code == P::s_ok && bytes != want && !rs_eof) {                // up to want bytes, as file.read's loop
        const fc::Reply g = fc::read(rs_fd, files + 4 + bytes, want - bytes); // op 0x2a
        if (g.outcome) code = rs_failed(g);
        else if (g.value < 0 || U32(g.value) > want - bytes) {
            read_stream_end();
            code = P::s_storage;
        } else if (!g.value) rs_eof = 1;
        else bytes += U32(g.value);
    }
    ++result.reads;
    result.read_bytes += bytes;
    result.last_status = code;
    if (code != P::s_ok) {
        status_only(P::t_read_stream, seq, code);
        return;
    }
    rs_at += bytes;
    rs_ms = now();
    P::put32(files, code);
    send(P::t_read_stream, seq, files, 4 + bytes);
    if (rs_eof) {                                                       // the close while the host takes the reply
        read_stream_end();
        rs_done = 1;
    }
}
