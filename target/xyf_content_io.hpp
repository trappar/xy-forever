// Included by xyf_content_job.hpp inside xyf::content.
template <class Io> static void abandon(Job& j, Io& io) {
    if (j.fd >= 0 && !io.lost()) io.close(j.fd);
    j.fd = -1; j.sub = 0;
}
// Whole file into t (at most cap bytes; more is e_size), its SHA-256 in j.digest. One request per call.
template <class Io> static uint32_t load(Job& j, Io& io, const char* path, Text& t, uint32_t cap) {
    if (j.sub == 0) {
        j.fd = io.open(path, false);
        if (j.fd < 0) {
            if (j.fd == -2 && !io.lost()) return x_missing;
            fail(j, io.lost() ? e_link : e_open, 0, path);
            j.fd = -1;
            return x_failed;
        }
        j.sha.reset(); t.length = 0; j.sub = 1;
        return x_more;
    }
    if (j.sub == 1) {
        const uint32_t room = cap - t.length, want = room < chunk ? room : chunk;
        const int got = io.read(j.fd, want ? t.data + t.length : j.buffer, want ? want : 1u);
        if (got < 0 || uint32_t(got) > (want ? want : 1u) || (!want && got > 0)) {
            const bool lost = io.lost();
            abandon(j, io);
            fail(j, lost ? e_link : got > 0 ? e_size : e_read, 0, path);
            return x_failed;
        }
        if (!got) { j.sub = 2; return x_more; }
        j.sha.update(t.data + t.length, uint32_t(got));
        t.length += uint32_t(got);
        return x_more;
    }
    const int c = io.close(j.fd);
    j.fd = -1; j.sub = 0;
    if (c) { fail(j, io.lost() ? e_link : e_read, 0, path); return x_failed; }
    j.sha.finish(j.digest);
    return x_done;
}
template <class Io> static uint32_t store_fail(Job& j, Io& io, uint32_t error, const char* path) {
    const bool lost = io.lost();
    abandon(j, io);
    if (!lost) io.unlink(j.part);                       // best effort; a cut leaves it (overwritten next time)
    fail(j, lost ? e_link : error, 0, path);
    return x_failed;
}
// data -> path + ".part", read back and compared, renamed over path. The caller checked both with `allowed`.
template <class Io> static uint32_t store(Job& j, Io& io, const char* path, const uint8_t* data, uint32_t n) {
    switch (j.sub) {
    case 0:
        if (!join(j.part, path, 0, 0, ".part")) { fail(j, e_path, 0, path); return x_failed; }
        j.fd = io.open(j.part, true);
        if (j.fd < 0) return store_fail(j, io, e_write, path);
        j.offset = 0; j.sub = 1;
        return x_more;
    case 1: {
        if (j.offset == n) { j.sub = 2; return x_more; }
        const uint32_t want = n - j.offset < chunk ? n - j.offset : chunk;
        const int put = io.write(j.fd, data + j.offset, want);
        if (put <= 0 || uint32_t(put) > want) return store_fail(j, io, e_write, path);
        j.offset += uint32_t(put);
        return x_more;
    }
    case 2: {
        const int c = io.close(j.fd);
        j.fd = -1;
        if (c) return store_fail(j, io, e_write, path);
        j.sub = 3;
        return x_more;
    }
    case 3:
        j.fd = io.open(j.part, false);
        if (j.fd < 0) return store_fail(j, io, e_verify, path);
        j.offset = 0; j.sub = 4;
        return x_more;
    case 4: {
        const int got = io.read(j.fd, j.buffer, chunk);
        if (got < 0 || uint32_t(got) > chunk || uint32_t(got) > n - j.offset) return store_fail(j, io, e_verify, path);
        if (!got) {
            if (j.offset != n) return store_fail(j, io, e_verify, path);
            j.sub = 5;
            return x_more;
        }
        uint32_t d = 0;
        for (uint32_t i = 0; i != uint32_t(got); ++i) d |= j.buffer[i] ^ data[j.offset + i];
        if (d) return store_fail(j, io, e_verify, path);
        j.offset += uint32_t(got);
        return x_more;
    }
    case 5: {
        const int c = io.close(j.fd);
        j.fd = -1;
        if (c) return store_fail(j, io, e_verify, path);
        j.sub = 6;
        return x_more;
    }
    case 6:                                              // a content file: the old copy away (the state says
        if (!same(path, record_path)) {                  // switching), then the new one in
            if (io.unlink(path) < 0) return store_fail(j, io, e_write, path);
            j.sub = 9;
            return x_more;
        }
        if (!j.record_on_disk) { j.sub = 8; return x_more; }   // the record: never absent without its .old
        if (io.unlink(record_old) < 0) return store_fail(j, io, e_write, path);   // a stale .old (record there)
        j.sub = 7;
        return x_more;
    case 7:
        if (io.rename(record_path, record_old) < 0) return store_fail(j, io, e_rename, path);
        j.sub = 8;
        return x_more;
    case 8:
        if (io.rename(j.part, record_path) < 0) return store_fail(j, io, e_rename, path);
        j.record_on_disk = true;
        j.sub = 10;
        return x_more;
    case 9:
        j.sub = 0;
        if (io.rename(j.part, path) < 0) return store_fail(j, io, e_rename, path);
        return x_done;
    default:                                             // the record's .old (a failure leaves it stale: unused,
        j.sub = 0;                                       // removed by the next record write)
        if (io.unlink(record_old) < 0) { fail(j, io.lost() ? e_link : e_write, 0, record_old); return x_failed; }
        return x_done;
    }
}

