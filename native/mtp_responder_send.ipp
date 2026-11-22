// Part of mtp_responder.cpp, included there at this point: not compiled alone.
// SendObject (#4): the data goes into an upload's temporary file (FileOps::upload; the target's name is untouched),
// closed, then committed: renamed onto its name in one step when every announced byte arrived, else removed. A cut
// cable or a power cut mid-upload leaves the old file (or none) under the name, never a truncated one; the
// temporary file is removed later (processor B's sweep at the next USB start). One file call per step.
bool Responder::send_object() {
    if (stage == 0) {                                             // open the upload (once)
        if (!failed && !pending_handle) failed = r_no_valid_object_info;
        if (!failed && !path_of(pending_handle, path)) failed = r_general_error;
        stage = 1;
        if (!failed) {
            const int r = ops.upload(ops.context, path, pending_size, pending_handle);
            if (r < 0) failed = r == f_refused ? U32(r_access_denied) : error_code(r);
            else {
                fd = r;
                opened = 1;
            }
            return true;
        }
    }
    if (stage == 1) {
        if (in_pos != in_len) {
            U32 n = in_len - in_pos;
            if (n > piece) n = piece;
            if (!failed) {
                const int r = ops.write(ops.context, fd, in + in_pos, n);
                if (r != int(n)) failed = r == f_refused ? r_access_denied : r_store_full;
                in_pos += n;
                return true;
            }
            in_pos = in_len;
            return true;
        }
        if (phase == p_data) return false;                       // wait for the next OUT transfer
        stage = 2;
    }
    if (stage == 2) {
        stage = 3;
        if (fd >= 0) {                                            // #144: the last piece's answer comes with it
            const int r = ops.close(ops.context, fd);
            if (r < 0 && !failed) failed = r == f_refused ? r_access_denied : r_store_full;
            fd = -1;
            return true;
        }
    }
    if (stage == 3 && opened) {                                   // onto its name, or removed
        opened = 0;
        if (!failed && data_total != pending_size) failed = r_incomplete_transfer;
        const int r = ops.commit(ops.context, path, !failed);
        if (!failed && r < 0) failed = r == f_refused ? U32(r_access_denied) : error_code(r);
        return true;
    }
    if (failed) {
        if (pending_handle && failed != r_no_valid_object_info) {
            if (pending_new) forget(pending_handle);
            else objects[pending_handle - 1].fresh = 0;           // the old file stays: GetObjectInfo stats it
        }
        respond(U16(failed));
        return true;
    }
    objects[pending_handle - 1].size = data_total;
    objects[pending_handle - 1].fresh = 1;
    pending_handle = 0;
    opened = 0;
    respond(r_ok);
    return true;
}
