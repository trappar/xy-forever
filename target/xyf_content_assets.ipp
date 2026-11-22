// Included in step() after the removal phase.
case st_copy: {                                      // the target's files
    if (j.index == j.list_count) {
        j.stage = st_catalog;
        j.leg = 0;
        note(j, catalog);
        break;
    }
    const Ref& f = j.list_refs[j.index];
    const uint8_t* rel = j.list.data + f.path;
    if (j.leg == 0) {                                // the trusted record's SHA-256 is the target's?
        ++j.compared;
        j.leg = 1;
        if (j.trusted) {
            const uint32_t at = lower(j.record, j.record_refs, j.record_count, rel, f.bytes);
            const Ref* had = at != j.record_count && !compare_ref(j.record, j.record_refs[at], rel, f.bytes) ?
                                 j.record_refs + at :
                                 0;
            uint32_t d = had && had->hash ? 0u : 1u;
            for (uint32_t i = 0; !d && i != 64; ++i) d |= j.record.data[had->hash + i] ^ j.list.data[f.hash + i];
            if (!d) {
                ++j.index;
                ++j.done;
                j.leg = 0;
            }
        }
        break;
    }
    if (j.leg == 1) {                                // else the live file: the target's already? (no write)
        join(p, root, rel, f.bytes);
        r = load(j, io, p, j.file, j.file.capacity);
        if (r == x_more || r == x_failed) break;
        if (r == x_done) ++j.read;
        if (r == x_done && hash_equal(j.list.data + f.hash, j.digest.bytes)) {
            ++j.index;
            ++j.done;
            j.leg = 0;
        } else j.leg = 2;
        break;
    }
    if (j.leg == 2) {                                // its folders, shallowest first
        uint32_t k = j.cursor;
        while (k < f.bytes && rel[k] != '/') ++k;
        if (k >= f.bytes) {
            j.leg = 3;
            j.cursor = 0;
            break;
        }
        j.cursor = k + 1;
        bool made = true;                            // a prefix of the last folder made
        for (uint32_t i = 0; made && i != k; ++i) made = j.folder[i] == char(rel[i]);
        if (made && (j.folder[k] == '/' || !j.folder[k])) break;      // (an official folder too: it may be gone)
        join(p, root, rel, k);
        if (!allowed(Use::make_folder, p, false)) {
            fail(j, e_path, 0, p);
            break;
        }
        if (io.mkdir(p) < 0) {
            fail(j, io.lost() ? e_link : e_write, 0, p);
            break;
        }
        join(j.folder, "", rel, k);
        break;
    }
    if (j.leg == 3) {                                // the entry's copy, its SHA-256 as listed
        if (!join(p, firmware, bytes(j.target), length(j.target), "/content/") ||
            !join(p + length(p), "", rel, f.bytes)) {
            fail(j, e_path, 0, j.target);
            break;
        }
        note(j, p);
        r = load(j, io, p, j.file, j.file.capacity);
        if (r == x_more || r == x_failed) break;
        if (r == x_missing) {
            fail(j, e_open, 0, p);
            break;
        }
        if (!hash_equal(j.list.data + f.hash, j.digest.bytes)) {
            fail(j, e_sha256, 0, p);
            break;
        }
        j.leg = 4;
        break;
    }
    join(p, root, rel, f.bytes);
    if (!allowed(Use::install, p, false)) {
        fail(j, e_path, 0, p);
        break;
    }
    if (j.leg == 4) {
        xy_forever::managed_blob_link::temporary(j.part, j.file.length);
        if (io.unlink(j.part) < 0) {
            fail(j, io.lost() ? e_link : e_write, 0, j.part);
            break;
        }
        j.leg = 5;
        break;
    }
    if (j.leg == 5) {
        const int staged = io.stage_blob_link(j.part, j.list.data + f.hash);
        if (staged < 0) {
            fail(j, io.lost() ? e_link : e_write, 0, j.part);
            break;
        }
        if (staged == 2 && j.cursor) {
            fail(j, e_verify, 0, j.part);
            break;
        }
        j.leg = staged == 2 ? 9 : staged == 1 ? 8 : 6;
        break;
    }
    if (j.leg == 6) {
        r = load(j, io, j.part, j.file, j.file.capacity);
        if (r == x_more || r == x_failed) break;
        if (r != x_done || !hash_equal(j.list.data + f.hash, j.digest.bytes)) {
            fail(j, e_sha256, 0, j.part);
            break;
        }
        j.leg = 7;
        break;
    }
    if (j.leg == 9) {
        xy_forever::managed_blob_link::temporary(j.part, j.file.length, true);
        if (io.unlink(j.part) < 0) {
            fail(j, io.lost() ? e_link : e_write, 0, j.part);
            break;
        }
        j.leg = 10;
        break;
    }
    if (j.leg == 10) {
        if (io.stage_blob_link(j.part, j.list.data + f.hash, true) != 0) {
            fail(j, io.lost() ? e_link : e_write, 0, j.part);
            break;
        }
        j.leg = 11;
        break;
    }
    if (j.leg == 11) {
        const int done = io.copy_blob_piece();
        if (done < 0) {
            fail(j, io.lost() ? e_link : e_write, 0, j.part);
            break;
        }
        if (done) j.leg = 12;
        break;
    }
    if (j.leg == 12) {
        r = load(j, io, j.part, j.file, j.file.capacity);
        if (r == x_more || r == x_failed) break;
        if (r != x_done || !hash_equal(j.list.data + f.hash, j.digest.bytes)) {
            fail(j, e_sha256, 0, j.part);
            break;
        }
        j.leg = 13;
        break;
    }
    if (j.leg == 13) {
        xy_forever::managed_blob_link::blob_path(p, reinterpret_cast<const char*>(j.list.data + f.hash));
        if (io.publish_blob_link(j.part, p) < 0) {
            fail(j, io.lost() ? e_link : e_rename, 0, p);
            break;
        }
        j.cursor = 1;                              // a second request to seal this blob is an error
        j.leg = 4;
        break;
    }
    if (j.leg == 7) {
        if (!join(p, firmware, bytes(j.target), length(j.target), "/content/") ||
            !join(p + length(p), "", rel, f.bytes)) {
            fail(j, e_path, 0, j.target);
            break;
        }
        if (io.publish_blob_link(j.part, p) < 0) {
            fail(j, io.lost() ? e_link : e_rename, 0, p);
            break;
        }
        j.leg = 14;
        break;
    }
    if (j.leg == 14) {
        // A replay can rename between two names of the same file. The scratch name may remain.
        if (io.unlink(j.part) < 0) {
            fail(j, io.lost() ? e_link : e_write, 0, j.part);
            break;
        }
        j.leg = 15;
        break;
    }
    if (j.leg == 15) {
        // The kept name was published from this verified blob in the same exclusive job.
        if (io.stage_blob_link(j.part, j.list.data + f.hash) != 0) {
            fail(j, io.lost() ? e_link : e_write, 0, j.part);
            break;
        }
        j.leg = 16;
        break;
    }
    if (j.leg == 16) {
        if (io.publish_blob_link(j.part, p) < 0) {
            fail(j, io.lost() ? e_link : e_rename, 0, p);
            break;
        }
    } else if (store(j, io, p, j.file.data, j.file.length) != x_done) break;
    ++j.index;
    ++j.done;
    ++j.written;
    j.bytes += j.file.length;
    j.leg = 0;
    j.cursor = 0;
    break;
}
