// Included by step() after removing assets absent from the target.
case st_copy: {
    if (j.index == j.list_count) {
        j.stage = st_catalog;
        j.leg = 0;
        note(j, catalog);
        break;
    }
    const Ref& f = j.list_refs[j.index];
    const uint8_t* rel = j.list.data + f.path;
    auto& a = j.asset;
    if (j.leg == 0) {
        if (!join(p, firmware, bytes(j.target), length(j.target), "/content/") ||
            !join(p + length(p), "", rel, f.bytes) || !managed_asset::begin(a, p, j.list.data + f.hash)) {
            fail(j, e_path, 0, j.target);
            break;
        }
        note(j, p);
        ++j.compared;
        j.leg = 1;
        break;
    }
    if (j.leg == 1) {
        const auto prepared = managed_asset::step(a, io);
        if (prepared == managed_asset::more) break;
        if (prepared != managed_asset::ready) {
            managed_asset::abandon(a, io);
            fail(j, io.lost() ? e_link : prepared == managed_asset::damaged ? e_sha256 : e_verify, 0, a.copy.source);
            break;
        }
        j.leg = a.bound ? 3 : 2;
        break;
    }
    if (j.leg == 2) {
        if (io.publish_blob_link(a.alias, a.copy.source) < 0) {
            fail(j, io.lost() ? e_link : e_rename, 0, a.copy.source);
            break;
        }
        j.leg = 3;
        break;
    }
    if (j.leg == 3) {
        join(p, root, rel, f.bytes);
        managed_asset::File live;
        const int found = io.stat_asset(p, live);
        if (found < 0) {
            fail(j, io.lost() ? e_link : e_open, 0, p);
            break;
        }
        if (!found && managed_asset::same_file(live, a.staged)) {
            j.leg = 7;
        } else {
            j.leg = 4;
            j.cursor = 0;
        }
        break;
    }
    if (j.leg == 4) { // make the parents shallowest first
        uint32_t k = j.cursor;
        while (k < f.bytes && rel[k] != '/') ++k;
        if (k >= f.bytes) {
            j.leg = a.bound ? 6 : 5;
            break;
        }
        j.cursor = k + 1;
        bool made = true;
        for (uint32_t i = 0; made && i != k; ++i) made = j.folder[i] == char(rel[i]);
        if (made && (j.folder[k] == '/' || !j.folder[k])) break;
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
    if (j.leg == 5) { // the first alias was consumed when the kept path was repaired
        if (io.stage_blob_link(a.alias, j.list.data + f.hash) != 0) {
            fail(j, io.lost() ? e_link : e_write, 0, a.alias);
            break;
        }
        j.leg = 6;
        break;
    }
    if (j.leg == 6) {
        join(p, root, rel, f.bytes);
        if (!allowed(Use::install, p, false)) {
            fail(j, e_path, 0, p);
            break;
        }
        if (io.publish_blob_link(a.alias, p) < 0) {
            fail(j, io.lost() ? e_link : e_rename, 0, p);
            break;
        }
        ++j.written;
    } else if (a.bound && io.unlink(a.alias) < 0) {
        fail(j, io.lost() ? e_link : e_write, 0, a.alias);
        break;
    }
    ++j.index;
    ++j.done;
    j.leg = 0;
    break;
}
