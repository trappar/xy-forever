// Publish and link the verified members only after closing the TFW reader. Each member is already a sealed file
// (mode 0444, written by B and hashed as it went): it becomes its blob by one rename, unless the blob is there.
// #279: a new blob takes three requests: the rename, the link, its rename over the member's name. The link's scratch
// name is removed first only when A refuses the link (one left by an import cut short).
U32 pack_import_begin_blobs(PackImportJob& j) {
    if (const U32 closed = j.source.close()) return closed;
    const int kind = pack_kind(j.io, "/yaffs2/xy-forever", "blobs");
    if (kind != 1 && kind != -2) return P::s_refused;
    if (kind == -2 && j.io.mkdir(xyf::blob::root)) return P::s_storage;
    j.member = 0;
    j.blob_step = pb_try;
    return P::s_ok;
}

U32 pack_import_blob(PackImportJob& j) {
    const auto& member = j.entries[j.member];
    if (member.directory || sp::same(member.name, sp::marker)) {
        ++j.member;
        return P::s_ok;
    }
    const uint8_t* const expected = j.references[pack_import_done];
    char path[file_engine::max_path], file[file_engine::max_path];
    if (!join(file, j.staging, "/", member.name + j.layout.relative_at)) return P::s_refused;
    switch (j.blob_step) {
    case pb_try: { // the member's file becomes the blob, unless one is there (A refuses: the target exists)
        ml::blob_path(path, reinterpret_cast<const char*>(expected));
        uint32_t error = 0;
        const U32 code = file_client::rename(file, path, error, mounted);
        if (code == P::s_ok) j.published = true;
        else if (code != P::s_state || error != file_client::eexist) {
            if (code == P::s_link) j.io.link = 1;
            break;
        }
        j.blob_step = pb_stage;
        return P::s_ok;
    }
    case pb_stage: {
        // A link to the sealed blob in the scratch folder (stage: 0); no blob yet (1); an unsealed one (2).
        ml::temporary(j.partial, member.size);
        const int status = j.io.stage_blob_link(j.partial, expected);
        if (status < 0 && !j.io.lost() && !j.cleared) {
            j.blob_step = pb_clear;
            return P::s_ok;
        }
        if (status < 0 || (j.published && status)) break;
        j.blob_step = status == 1 ? pb_publish : status == 2 ? pb_replace : j.published ? pb_link : pb_open;
        return P::s_ok;
    }
    case pb_clear: // the link's scratch name taken: removed, then the link again
        if (j.io.unlink(j.partial) < 0) break;
        j.cleared = true;
        j.blob_step = pb_stage;
        return P::s_ok;
    case pb_replace: // an unsealed blob (never linked): this member's file replaces it
        ml::blob_path(path, reinterpret_cast<const char*>(expected));
        if (j.io.unlink(path) < 0) break;
        j.blob_step = pb_publish;
        return P::s_ok;
    case pb_publish:
        ml::blob_path(path, reinterpret_cast<const char*>(expected));
        if (j.io.rename(file, path)) break;
        j.published = true;
        j.blob_step = pb_stage;
        return P::s_ok;
    case pb_open: // a blob that was there: read it back before linking it
        j.fd = j.io.open(j.partial, false);
        if (j.fd < 0) break;
        j.hash.reset();
        j.verified_bytes = 0;
        j.blob_step = pb_read;
        return P::s_ok;
    case pb_read: {
        const auto read = file_client::read(j.fd, j.pending, sizeof(j.pending));
        if (read.outcome) {
            j.io.fail(read);
            break;
        }
        if (read.value < 0 || U32(read.value) > member.size - j.verified_bytes) break;
        if (read.value) {
            j.verified_bytes += U32(read.value);
            return j.hash.update(j.pending, U32(read.value)) ? P::s_ok : P::s_sha;
        }
        if (j.io.close(j.fd)) break;
        j.fd = -1;
        pack_hash::Digest digest;
        if (j.verified_bytes != member.size || !j.hash.finish(digest)) return P::s_sha;
        for (U32 i = 0; i != 32; ++i) {
            const char* const hex = "0123456789abcdef";
            if (hex[digest.bytes[i] >> 4] != expected[2 * i] || hex[digest.bytes[i] & 15] != expected[2 * i + 1])
                return P::s_sha;
        }
        j.blob_step = pb_link;
        return P::s_ok;
    }
    case pb_link: // over this member's own file when the blob was there
        if (j.io.publish_blob_link(j.partial, file)) break;
        j.partial[0] = 0;
        j.published = j.cleared = false;
        ++j.member;
        ++pack_import_done;
        j.blob_step = pb_try;
        return P::s_ok;
    }
    return j.io.lost() ? P::s_link : P::s_storage;
}
