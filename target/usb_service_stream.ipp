// Part of usb_service.cpp, included there at this point: not compiled alone.
// #4: streamed, replace-safe file writes (file.write_begin / write_data / write_end, protocol 1.18). The bytes go
// straight into A's storage, a piece at a time, as /yaffs2/xy-forever/tmp/put-<size>-<crc> (file_engine::upload_name):
// no staging, so any size fits. At the end B checks the received bytes' CRC-32 and SHA-256, reads the file back over
// the read path and compares its CRC-32 (A gets no new stock call), then asks A to rename it onto the target with
// rename_over: A checks the upload's size against its name and replaces the target in one yaffs rename
// (storage_server_marker.ipp rename_now). A cut cable or a power cut leaves the target old or new; only the tmp file
// can be left, and it is removed when the abandoned write is noticed (stream_turn, the next write_begin) or at the
// next USB start (boot_sweep_turn).
volatile U32 stream_open __attribute__((section(".data"))) = 0;       // a streamed write is in progress
volatile U32 stream_done __attribute__((section(".data"))) = 0;       // the last write_end succeeded (its retry: ok)
volatile U32 stream_ms __attribute__((section(".data"))) = 0;         // now() at the last piece
int stream_fd __attribute__((section(".data"))) = -1;
uint32_t stream_size __attribute__((section(".data"))) = 0, stream_crc __attribute__((section(".data"))) = 0;
uint32_t stream_received __attribute__((section(".data"))) = 0, stream_running __attribute__((section(".data"))) = 0;
uint32_t stream_back __attribute__((section(".data"))) = 0, stream_error __attribute__((section(".data"))) = 0;
uint8_t stream_sha[32] __attribute__((section(".data"))) = {0};
char stream_tmp[file_engine::upload_bytes] __attribute__((section(".data"))) = {0};
char stream_target[file_engine::max_path] __attribute__((section(".data"))) = {0};
U32 stream_hash_storage[(sizeof(xyf_core::hash::Sha256) + 3) / 4] __attribute__((section(".data"))) = {0};
xyf_core::hash::Sha256& stream_hash() { return *reinterpret_cast<xyf_core::hash::Sha256*>(stream_hash_storage); }
static const U32 stream_idle_ms = 5000;                                 // below window_settle_ms: B abandons first

// An upload is in progress: a staging still receiving, or a streamed write.
bool uploading() { return (put_expected && result.put_received != put_expected) || stream_open; }

// Closes the tmp file and removes it (bounded requests; a request left out: it stays for the sweep).
void stream_abandon() {
    if (!stream_open) return;
    stream_open = 0;
    if (mounted == file_client::mounted_stale) return;
    file_client::close_write(stream_fd, mounted);
    file_client::remove_partial(stream_tmp, mounted);
    writes_due = 1;
    writes_ms = now();
}
void stream_turn() {                                                     // the menu loop: a write left without data
    if (stream_open && now() - stream_ms > stream_idle_ms) stream_abandon();
}

// FILE_WRITE_BEGIN: path NUL + u32 size + u32 crc + 32-byte SHA-256. Reply: u32 status.
void write_begin(uint8_t seq, const uint8_t* payload, U32 length) {
    stream_abandon();
    stream_done = 0;
    U32 n = 0;
    while (n < length && payload[n]) ++n;
    const char* path = reinterpret_cast<const char*>(payload);
    U32 code = P::s_ok;
    if (n + 1 + 40 != length || n + 1 > storage_server::max_path) code = P::s_bad_request;
    else if (!storage_server::valid_write_path(path)) code = P::s_refused;
    else if ((code = enter_writes()) == P::s_ok) {
        storage_used = 1;
        stream_size = P::get32(payload + n + 1);
        stream_crc = P::get32(payload + n + 5);
        for (U32 i = 0; i != 32; ++i) stream_sha[i] = payload[n + 9 + i];
        for (U32 i = 0; i <= n; ++i) stream_target[i] = path[i];
        file_engine::upload_name(stream_tmp, stream_size, stream_crc);
        say("Writing a file...", path, "Do not unplug or power off.");
        code = file_client::open_write(stream_tmp, stream_fd, mounted);   // op 8, mkdir -p, open "wb"
        if (code == P::s_ok && (code = file_client::make_folders(path, mounted)) != P::s_ok) {
            if (mounted != file_client::mounted_stale) file_client::close_write(stream_fd, mounted);
        }
        if (code == P::s_ok) {
            stream_open = 1;
            stream_received = 0;
            stream_running = 0;
            stream_hash().reset();
        }
        writes_due = 1;
        writes_ms = stream_ms = now();
    }
    result.last_status = code;
    status_only(P::t_write_begin, seq, code);
}

// FILE_WRITE_DATA: u32 offset + bytes, written to the tmp file before the reply. Reply: u32 status, u32 received.
void write_data(uint8_t seq, const uint8_t* payload, U32 length) {
    if (!stream_open || length < 5) {
        status_only(P::t_write_data, seq, P::s_state);
        return;
    }
    const U32 offset = P::get32(payload), n = length - 4;
    U32 code = P::s_ok;
    if (offset < stream_received && offset + n <= stream_received) {
    }  // a resend: file.write_end checks the file
    else if (offset != stream_received || offset + n > stream_size)
        code = P::s_range;
    else {
        uint32_t written = 0;
        code = file_client::write_more(stream_fd, payload + 4, n, written, mounted);
        stream_received += written;
        if (code == P::s_ok) {
            stream_running = xyf::crc::update(stream_running, payload + 4, n);
            stream_hash().update(payload + 4, n);
        } else stream_abandon();
        writes_due = 1;
        writes_ms = stream_ms = now();
    }
    result.last_status = code;
    if (code != P::s_ok) {
        status_only(P::t_write_data, seq, code);
        return;
    }
    uint8_t p[8];
    P::put32(p, code);
    P::put32(p + 4, stream_received);
    send(P::t_write_data, seq, p, 8);
}

// FILE_WRITE_END: close, the received bytes' CRC-32 and SHA-256, the read-back CRC-32, then A's rename_over onto
// the target. Any failure removes the tmp file. Reply: u32 status, u32 written, u32 read-back CRC, u32 A's error.
void write_end(uint8_t seq) {
    U32 code = P::s_ok;
    if (stream_done) {
    }                                                  // a retry after a lost reply
    else if (!stream_open || stream_received != stream_size)
        code = P::s_state;
    else {
        stream_open = 0;
        stream_back = 0;
        stream_error = 0;
        xyf_core::hash::Digest digest;
        code = file_client::close_write(stream_fd, mounted);
        if (code == P::s_ok && stream_running != stream_crc) code = P::s_crc;
        else if (code == P::s_ok && (!stream_hash().finish(digest) || !sha_equal(digest.bytes, stream_sha)))
            code = P::s_sha;
        uint32_t total = 0;
        if (code == P::s_ok) code = file_client::read_crc(stream_tmp, stream_size, total, stream_back, mounted);
        if (code == P::s_ok && (total != stream_size || stream_back != stream_crc)) code = P::s_crc;
        if (code == P::s_ok) code = file_client::rename(stream_tmp, stream_target, stream_error, mounted, true);
        if (code != P::s_ok && mounted != file_client::mounted_stale) file_client::remove_partial(stream_tmp, mounted);
        stream_done = code == P::s_ok;
        writes_due = 1;
        writes_ms = now();
        say(code == P::s_ok ? "File written and verified." : "File write FAILED.", stream_target);
    }
    uint8_t p[16];
    P::put32(p, code);
    P::put32(p + 4, stream_received);
    P::put32(p + 8, stream_back);
    P::put32(p + 12, stream_error);
    result.last_status = code;
    send(P::t_write_end, seq, p, 16);
}
