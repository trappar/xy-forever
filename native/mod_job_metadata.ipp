namespace record = xyf::package_record;
// The original list remains the source of digests. The two metadata entries
// live at the end of the table, outside the content step's file count.
Error parse_list(Job& j, Job::Package& p) {
    Byte* t = p.list;
    U32 lines = 0;
    for (U32 i = 0; i != p.list_bytes; ++i)
        if (t[i] == '\n') ++lines;
    if (p.list_bytes && t[p.list_bytes - 1] != '\n') return Error::list;
    p.files = reinterpret_cast<mod_content::File*>(take(j, lines * U32(sizeof(mod_content::File)), true));
    if (!p.files && lines) return Error::region;
    p.file_count = 0;
    U32 metadata_at = lines;
    for (U32 s = 0; s != p.list_bytes;) {
        U32 e = s;
        while (t[e] != '\n') ++e;
        if (e - s < 67 || t[s + 64] != ' ' || t[s + 65] != ' ') return Error::list;
        t[e] = 0;
        const char* path = reinterpret_cast<const char*>(t + s + 66);
        const hash::Digest** metadata = same(path, record::native_name) ? &p.native_sha256 :
                                        same(path, record::legacy_name) ? &p.info_sha256 :
                                                                          0;
        if (metadata && *metadata) return Error::list;
        mod_content::File& file = p.files[metadata ? --metadata_at : p.file_count++];
        file.path = path;
        for (U32 k = 0; k != 32; ++k) {
            const int hi = hex(t[s + 2 * k]), lo = hex(t[s + 2 * k + 1]);
            if (hi < 0 || lo < 0) return Error::list;
            file.sha256.bytes[k] = Byte(hi << 4 | lo);
        }
        if (metadata) *metadata = &file.sha256;
        s = e + 1;
    }
    return Error::none;
}
const hash::Digest* listed(const Job::Package& p, const char* path) {
    for (U32 i = 0; i != p.file_count; ++i)
        if (same(p.files[i].path, path)) return &p.files[i].sha256;
    return 0;
}

struct MetadataScratch {
    Byte raw[json::document_bytes];
    record::Workspace parse;
    char view[xyf::manifest::max_text];
};

Phase begin_metadata(Job& j, Job::Package& p) {
    if (!p.metadata) {
        set_detail(j, p.name, record::native_name);
        if (!compose(j, j.input->generation, p.name, record::native_name, 0)) return fail(j, Error::path);
        const int probe = j.files->probe_metadata(j.files->context, j.path);
        if (probe == probe_again) return j.phase;
        if (probe == missing_file && !p.native_sha256) p.metadata = record::legacy_name;
        else if (probe == 0) p.metadata = record::native_name;
        else return fail(j, Error::open);
        return j.phase;
    }
    const bool native = p.metadata == record::native_name;
    const hash::Digest* want = native ? p.native_sha256 : p.info_sha256;
    set_detail(j, p.name, p.metadata);
    if (!want) return fail(j, Error::unlisted);
    if (!compose(j, j.input->generation, p.name, p.metadata, 0)) return fail(j, Error::path);
    auto* scratch = native ? reinterpret_cast<MetadataScratch*>(take(j, sizeof(MetadataScratch), false)) : 0;
    if (native && !scratch) return fail(j, Error::region);
    const Error e = begin_load(j, want, scratch ? scratch->raw : 0, json::document_bytes, true);
    return e == Error::none ? j.phase : fail(j, e);
}

Error finish_metadata(Job& j, Job::Package& p, xyf::manifest::Type expected) {
    if (p.metadata == record::legacy_name) {
        p.info = j.load.data;
        p.info_bytes = j.load.size;
        return Error::none;
    }
    auto* scratch = reinterpret_cast<MetadataScratch*>(j.load.data);
    uint32_t bytes = 0;
    const json::Status status = record::view(reinterpret_cast<const char*>(scratch->raw), j.load.size, scratch->parse,
                                             expected, scratch->view, bytes);
    if (status != json::ok) {
        set_detail(j, p.name, p.metadata, json::message(status));
        return Error::info;
    }
    // The raw bytes have passed their hash check. Retain just the view, at the
    // start of the same workspace and separate from the source of this copy.
    p.info = take(j, bytes, true);
    p.info_bytes = bytes;
    if (!p.info) return Error::region;
    for (U32 i = 0; i != bytes; ++i) p.info[i] = Byte(scratch->view[i]);
    return Error::none;
}
