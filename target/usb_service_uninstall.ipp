// The confirmed Settings action and USB command share this job. B owns the staged bytes and
// pumps it without a host. Up to the verified stock boot write the boot partition is untouched
// and the manager tree remains available. Only after read-back may that tree be removed.
enum UninstallStep : U32 {
    un_idle,
    un_root,
    un_tmp,
    un_source,
    un_read,
    un_restore,
    un_switching,
    un_tidy,
    un_tidying,
    un_root_check,
    un_root_read,
    un_make,
    un_copy,
    un_back,
    un_publish,
    un_close,
    un_hash_stage,
    un_flash,
    un_remove,
    un_sync,
    un_done,
    un_failed
};
struct UninstallJob {
    UninstallStep step;
    U32 id, started, ended, at, crc, code, skip;
    int fd;
    bool manager, root_image, content, dismissed;
    const char* detail;
    char upload[file_engine::upload_bytes];
    Tree tree;
    ContentIo io;
};
UninstallJob uninstall_job __attribute__((section(".data"))) = {};
constexpr char root_image_path[] = "/yaffs2/os.ldr";
bool uninstall_active() { return uninstall_job.step != un_idle && uninstall_job.step != un_failed; }
bool uninstall_owned() { return uninstall_active() || (uninstall_job.step == un_failed && job_kind == uninstall_kind); }
void uninstall_stage(UninstallStep next, const char* detail) {
    uninstall_job.step = next;
    uninstall_job.detail = detail;
    uninstall_job.at = 0;
}
void uninstall_fail(U32 code, const char* detail) {
    auto& u = uninstall_job;
    if (u.fd >= 0 && !u.io.lost()) u.io.close(u.fd);
    u.fd = -1;
    u.code = code;
    u.ended = now();
    result.put_ok = put_expected = 0;
    settle_begin();
    job_state = job_ended;
    job_kind = uninstall_kind;
    uninstall_stage(un_failed, detail);
}
U32 uninstall_begin() {
    if (!menu_usb) return P::s_refused; // the stock service identity gate did not open
    stop_idle_gc();
    if (uninstall_active() || boot_probe_active() || rehearsal_active() || recovery_pending || job_running() ||
        closing || uploading() || file_client::left_out() XYF_OR_MTP_BUSY || xy_forever::mods_io::converging())
        return P::s_busy;
    if (!a_serving()) return P::s_link;
    const U32 code = file_client::mount_ready(mounted);
    if (code != P::s_ok) return code;
    auto& u = uninstall_job;
    u.id = ++job_counter;
    u.started = now();
    u.ended = u.code = u.crc = u.skip = u.io.link = 0;
    u.manager = u.root_image = u.content = u.dismissed = false;
    uninstall_close_failed = false;
    u.fd = -1;
    result.put_ok = put_expected = 0;
    job_kind = uninstall_kind;
    job_state = job_busy;
    job_usb = 0;
    uninstall_stage(un_root, "Checking device files");
    return P::s_ok;
}
void uninstall_command(uint8_t seq, const uint8_t* payload, U32 length) {
    const U32 code = length == 4 && P::get32(payload) == 1 ? uninstall_begin() : U32(P::s_bad_request);
    if (code) {
        status_only(P::t_uninstall_start, seq, code);
        return;
    }
    uint8_t p[24] = {};
    P::put32(p + 4, uninstall_job.id);
#if defined(XYF_MTP_SESSION)
    const char* serial = unit_serial();
    for (U32 i = 0; i < 16 && serial[i]; ++i) p[8 + i] = serial[i];
#endif
    send(P::t_uninstall_start, seq, p, sizeof(p));
}
void uninstall_view(JobView& v) {
    const auto& u = uninstall_job;
    v.id = u.id;
    v.phase = u.step == un_done    ? xc::phase_done :
              u.step == un_failed  ? xc::phase_failed :
              u.step < un_restore  ? xc::phase_inputs :
              u.step <= un_tidying ? xc::phase_content :
                                     xc::phase_finish;
    v.total = u.step == un_read || u.step == un_copy || u.step == un_back ||
                      (u.step == un_root_read || u.step == un_hash_stage) ?
                  stock_os_bytes :
                  0;
    v.done = v.total ? u.at : 0;
    v.error = u.code ? (u.code == P::s_link ? xc::e_link : xc::e_verify) : 0u;
    v.status = u.code;
    v.state = u.step == un_done || u.step == un_failed ? job_ended : job_busy;
    v.debris = v.commit = 0;
}
void uninstall_status(uint8_t seq, U32 want) {
    const auto& u = uninstall_job;
    if (want && want != u.id) {
        status_only(P::t_job_status, seq, P::s_not_found);
        return;
    }
    JobView v;
    uninstall_view(v);
    uint8_t p[80] = {};
    const U32 fields[] = {v.id, v.phase, v.done, v.total, v.error, v.status, (u.ended ? u.ended : now()) - u.started};
    for (U32 i = 0; i < sizeof(fields) / sizeof(*fields); ++i) P::put32(p + 4 + 4 * i, fields[i]);
    for (U32 i = 0; i < 31 && u.detail[i]; ++i) p[48 + i] = u.detail[i];
    send(P::t_job_status, seq, p, sizeof(p));
}
bool uninstall_close() {
    auto& u = uninstall_job;
    const int fd = u.fd;
    u.fd = -1;
    return u.io.close(fd) == 0;
}
// Check directory-entry types (stat follows links) before opening any path for writing or
// walking the manager tree. An existing upload is unlinked before open so a hardlink is safe too.
void uninstall_list(UninstallJob& u) {
    char name[file_client::max_name + 1];
    uint32_t kind = 0;
    const bool root = u.step == un_root;
    const int found = u.io.list_one(root ? "/yaffs2" : xa::root, u.skip++, name, kind);
    if (found < 0) return uninstall_fail(P::s_storage, "Cannot list device files");
    if (found) {
        if (root && same_text(name, "os.ldr")) {
            if (kind != 0) return uninstall_fail(P::s_refused, "Root OS is not a regular file");
            u.root_image = true;
        }
        if (root && same_text(name, "content")) {
            if (kind != 1) return uninstall_fail(P::s_refused, "Invalid content folder");
            u.content = true;
        }
        if (root && same_text(name, "xy-forever")) {
            if (kind != 1) return uninstall_fail(P::s_refused, "Invalid XY Forever folder");
            u.manager = true;
        }
        if (!root && same_text(name, "tmp") && kind != 1)
            return uninstall_fail(P::s_refused, "Invalid temporary folder");
        return;
    }
    if (root && !u.content) return uninstall_fail(P::s_not_found, "Missing content folder");
    u.skip = 0;
    u.step = root && u.manager ? un_tmp : un_source;
}
// The content switch restores the originally saved official files and journal. A clean install,
// or a retry after removing the library, may already have official content.
void uninstall_restore(UninstallJob& u) {
    uint32_t got = 0;
    U32 code = file_client::read_file(xc::record_path, 0, true, files, xc::head_bytes, got, mounted);
    xc::State state;
    bool official = false;
    if (code == P::s_ok) {
        xc::parse(files, got, state);
        official = state.kind == xc::official;
    } else if (code == P::s_not_found) {
        code = file_client::read_file(xc::record_old, 0, true, files, xc::head_bytes, got, mounted);
        official = code == P::s_not_found;
    }
    if (code != P::s_ok && code != P::s_not_found) return uninstall_fail(code, "Cannot read content state");
    if (official && !journal_present) {
        u.step = un_tidy;
        return;
    }
    const char* ids[] = {"opxy-121", "opxy-121-stock"};
    for (const char* id : ids) {
        bool found = false;
        if (!official_121(id, found)) continue;
        job_state = job_ended; // only in this call, to hand the slot to the content job
        code = content_begin(id, true, true);
        if (code) return uninstall_fail(code, "Cannot restore official content");
        u.step = un_switching;
        return;
    }
    uninstall_fail(P::s_not_found, "Missing official library entry");
}
void uninstall_step(UninstallJob& u) {
    switch (u.step) {
    case un_root:
    case un_tmp: return uninstall_list(u);
    case un_source:
        u.fd = u.io.open(xa::source, false);
        if (u.fd < 0) return uninstall_fail(P::s_not_found, "Missing official OS 1.2.1");
        put_hash().reset();
        uninstall_stage(un_read, "Checking official OS 1.2.1");
        return;
    case un_read:
    case un_root_read:
    case un_back: {
        const bool source = u.step == un_read;
        uint8_t* piece = source ? staged + u.at : files;
        const auto r = file_client::read(u.fd, piece, file_client::piece_max);
        if (r.outcome) {
            u.io.fail(r);
            return uninstall_fail(P::s_link, "Lost storage link reading OS");
        }
        if (r.value < 0 || U32(r.value) > file_client::piece_max)
            return uninstall_fail(P::s_storage, "Cannot read OS file");
        const U32 n = U32(r.value);
        bool match = n <= stock_os_bytes - u.at;
        if (match && source) {
            put_hash().update(piece, n);
            u.crc = xyf::crc::update(u.crc, piece, n);
        } else if (match) {
            for (U32 i = 0; match && i < n; ++i) match = piece[i] == staged[u.at + i];
        }
        if (match && n) {
            u.at += n;
            return;
        }
        match = match && u.at == stock_os_bytes;
        if (!uninstall_close()) return uninstall_fail(P::s_storage, "Cannot close OS file");
        if (source) {
            xyf_core::hash::Digest d;
            if (!match || !put_hash().finish(d) || !sha_equal(d.bytes, stock_os_sha256))
                return uninstall_fail(P::s_sha, "OS differs from stock 1.2.1");
            for (U32 i = 0; i < 32; ++i) put_sha[i] = d.bytes[i];
            if (validate_image(stock_os_bytes, true)) return uninstall_fail(P::s_image, "Invalid stock OS image");
            uninstall_stage(un_restore, "Restoring official content");
        } else if (u.step == un_root_read) {
            uninstall_stage(match ? un_close : un_make, match ? "Preparing boot writer" : "Restoring root OS file");
        } else {
            if (!match) return uninstall_fail(P::s_sha, "Root OS verification failed");
            uninstall_stage(un_publish, "Publishing verified stock OS");
        }
        return;
    }
    case un_restore: return uninstall_restore(u);
    case un_switching:
        if (job_state == job_busy) return;
        if (uninstall_close_failed) return uninstall_fail(P::s_storage, "Storage close failed");
        if (cslot().job.stage != xc::st_done) return uninstall_fail(P::s_storage, "Official content restore failed");
        job_kind = uninstall_kind;
        job_state = job_busy;
        u.step = un_tidy;
        return;
    case un_tidy: {
        job_state = job_ended;
        const U32 code = adopt_begin("opxy-121", adopt_uninstall_tidy);
        if (code) return uninstall_fail(code, "Cannot check official content");
        uninstall_stage(un_tidying, "Checking stock content");
        return;
    }
    case un_tidying:
        if (job_state == job_busy) return;
        if (uninstall_close_failed) return uninstall_fail(P::s_storage, "Storage close failed");
        if (aslot().error)
            return uninstall_fail(aslot().code ? aslot().code : U32(P::s_storage), "Stock content check failed");
        job_kind = uninstall_kind;
        job_state = job_busy;
        uninstall_stage(un_root_check, "Checking root OS file");
        return;
    case un_root_check:
        if (!u.root_image) {
            u.step = un_make;
            return;
        }
        if (!a_serving() || file_client::mount_ready(mounted) != P::s_ok ||
            (u.fd = u.io.open(root_image_path, false)) < 0)
            return uninstall_fail(P::s_storage, "Cannot open root OS file");
        u.step = un_root_read;
        return;
    case un_make: {
        file_engine::upload_name(u.upload, stock_os_bytes, u.crc);
        U32 code = enter_writes();
        if (!code) code = file_client::mount_ready(mounted);
        if (code) return uninstall_fail(code, "Cannot open storage for writes");
        storage_used = writes_due = 1;
        writes_ms = now();
        if (file_client::make_folders(u.upload, mounted) != P::s_ok || u.io.unlink(u.upload) < 0 ||
            (u.fd = u.io.open(u.upload, true)) < 0)
            return uninstall_fail(P::s_storage, "Cannot create temporary OS");
        u.manager = true;
        uninstall_stage(un_copy, "Restoring root OS file");
        return;
    }
    case un_copy: {
        if (u.at < stock_os_bytes) {
            const U32 n = stock_os_bytes - u.at > file_client::chunk ? file_client::chunk : stock_os_bytes - u.at;
            if (u.io.write(u.fd, staged + u.at, n) != int(n))
                return uninstall_fail(P::s_storage, "Root OS write failed");
            u.at += n;
            return;
        }
        if (!uninstall_close() || (u.fd = u.io.open(u.upload, false)) < 0)
            return uninstall_fail(P::s_storage, "Cannot verify restored OS");
        uninstall_stage(un_back, "Verifying restored OS file");
        return;
    }
    case un_publish:
        if (u.io.rename(u.upload, root_image_path, true) < 0)
            return uninstall_fail(P::s_storage, "Cannot publish root OS file");
        u.step = un_close;
        return;
    case un_close:
        // Close the filesystem before entering install mode. Keep the library until read-back.
        settle_begin();
        put_hash().reset();
        uninstall_stage(un_hash_stage, "Checking staged stock OS");
        return;
    case un_hash_stage: {
        if (closing) return;
        if (uninstall_close_failed || mounted == file_client::mounted_stale)
            return uninstall_fail(P::s_storage, "Storage close failed");
        xyf_core::hash::Digest d;
        if (u.at < stock_os_bytes) {
            const U32 n = stock_os_bytes - u.at < 8192 ? stock_os_bytes - u.at : 8192;
            put_hash().update(staged + u.at, n);
            u.at += n;
            return;
        }
        if (!put_hash().finish(d) || !sha_equal(d.bytes, stock_os_sha256))
            return uninstall_fail(P::s_sha, "Staged OS verification failed");
        uninstall_stage(un_flash, "Writing stock boot partition");
        return;
    }
    case un_flash: {
        result.put_received = result.put_size = put_expected = stock_os_bytes;
        result.put_crc = u.crc;
        result.put_ok = 1;
        const U32 code = write_staged(stock_os_sha256, 32, true);
        if (code || result.install_written != stock_os_bytes || !result.verify_partition ||
            result.verify_sectors != stock_os_bytes / 512 + 1)
            return uninstall_fail(code ? code : U32(P::s_install), "Stock boot verification failed");
        // The stock writer switches partitions; reopen the user filesystem afterwards.
        mounted = 0;
        if (!a_serving()) return uninstall_fail(P::s_link, "Stock written; link lost");
        join(u.tree.path, xa::root, "");
        u.tree.root = text_bytes(xa::root);
        uninstall_stage(u.manager ? un_remove : un_sync, "Removing XY Forever files");
        return;
    }
    case un_remove: {
        const U32 step = tree_step(u.tree, files, true);
        if (step == tree_failed) return uninstall_fail(P::s_storage, "Stock written; cleanup failed");
        if (step == tree_done) {
            settle_quiet = 1;
            settle_begin();
            u.step = un_sync;
        }
        return;
    }
    case un_sync:
        if (closing) return;
        if (uninstall_close_failed || mounted == file_client::mounted_stale)
            return uninstall_fail(P::s_storage, "Stock written; close failed");
        u.ended = now();
        uninstall_stage(un_done, "Restarting into stock OS 1.2.1");
        return;
    default: return;
    }
}
bool uninstall_turn() {
    auto& u = uninstall_job;
    if (!uninstall_active()) return false;
    if (u.step == un_done) {
        if (now() - u.ended < 3000u) return false; // the host can read the final job result
        u.step = un_idle; // no repeated restart on a lost link
        job_state = job_ended;
        return true;
    }
    const U32 start = now();
    do {
        const auto previous = u.step;
        uninstall_step(u);
        if (previous != u.step || u.step == un_switching || u.step == un_tidying || closing) break;
    } while (now() - start < job_slice_ms);
    return false;
}
