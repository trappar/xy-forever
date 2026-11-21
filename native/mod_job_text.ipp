// Included by mod_job.cpp inside its private namespace.
struct Text {
    Byte* at; U32 size, capacity; bool ok;
    __attribute__((noinline)) void byte(Byte c) { if (size == capacity) ok = false; else at[size++] = c; }
    __attribute__((noinline)) void bytes(const Byte* s, U32 n) { for (U32 i = 0; i != n; ++i) byte(s[i]); }
    __attribute__((noinline)) void text(const char* s) { while (*s) byte(Byte(*s++)); }
    __attribute__((noinline)) void number(U32 v) {
        static const U32 powers[10] = {1000000000u, 100000000u, 10000000u, 1000000u, 100000u, 10000u, 1000u, 100u,
                                       10u, 1u};
        bool started = false;
        for (U32 k = 0; k != 10; ++k) {
            Byte d = '0';
            while (v >= powers[k]) { v -= powers[k]; ++d; }
            if (d != '0' || started || k == 9) { byte(d); started = true; }
        }
    }
    __attribute__((noinline)) void hex(const Byte* b, U32 n) {
        for (U32 i = 0; i != n; ++i) { byte(Byte("0123456789abcdef"[b[i] >> 4])); byte(Byte("0123456789abcdef"[b[i] & 15u])); }
    }

};
// [a-z0-9][a-z0-9._-]*, at most 31 bytes (manifest generation, profile).
bool token(const Byte* v, U32 n) {
    if (!n || n > 31) return false;
    for (U32 i = 0; i != n; ++i) {
        const Byte c = v[i];
        const bool ok = (c >= 'a' && c <= 'z') || (c >= '0' && c <= '9') || (i && (c == '.' || c == '_' || c == '-'));
        if (!ok) return false;
    }
    return true;
}
// "<64 hex>  <path>\n"
void list_line(Text& t, const hash::Digest& d, const char* path) {
    t.hex(d.bytes, 32); t.text("  "); t.text(path); t.byte('\n');
}
// Same compact document and field order as package_records.document in the host writer.
void manifest_text(Text& t, const Job& j, const Byte* generation, U32 generation_bytes,
                   const Byte* profile, U32 profile_bytes) {
    t.text("{\"schema\":\"xy-forever.package\",\"schema_version\":1,\"type\":\"firmware\",\"name\":\"");
    for (const char* p = j.input->name; *p; ++p) {
        if (*p == '"' || *p == '\\') t.byte('\\');
        t.byte(Byte(*p));
    }
    t.text("\",\"kind\":\"open-xy\",\"bytes\":"); t.number(j.ldr_bytes);
    const Byte crc[4] = {Byte(j.crc >> 24), Byte(j.crc >> 16), Byte(j.crc >> 8), Byte(j.crc)};
    t.text(",\"crc32\":\""); t.hex(crc, 4);
    t.text("\",\"sha256\":\""); t.hex(j.ldr_sha256.bytes, 32);
    t.text("\",\"needs_os\":\""); t.hex(j.profile.stock_sha256.bytes, 32);
    t.text("\",\"generation\":\""); t.bytes(generation, generation_bytes);
    t.text("\",\"profile\":\""); t.bytes(profile, profile_bytes);
    t.text("\",\"machines\":"); t.number(j.input->selection.machine_count);
    t.text("}\n");
}
// The entry's manifest, completed files.sha256, their hashes and the output sizes.
__attribute__((noinline)) Error entry_texts(Job& j) {
    const Job::Package& base = j.packages[0];
    const Byte *generation, *profile; U32 generation_bytes, profile_bytes;
    if (!value_of(base.info, base.info_bytes, "generation", generation, generation_bytes) || !token(generation, generation_bytes)) {
        set_detail_text(j, "generation"); return Error::base;
    }
    if (!value_of(base.info, base.info_bytes, "profile", profile, profile_bytes) || !token(profile, profile_bytes)) {
        set_detail_text(j, "profile"); return Error::base;
    }
    const U32 count = j.builder.listed_count + fixed_outputs;
    j.out_sizes = reinterpret_cast<U32*>(take(j, count * 4u, true));
    j.info_out = j.out_sizes ? take(j, info_bytes, true) : 0;
    if (!j.info_out) { set_detail_text(j, "entry"); return Error::region; }
    j.out_count = count;
    j.ldr_bytes = j.patcher.total;
    copy_digest(j.ldr_sha256, j.patcher.sha256);
    Text t = {j.info_out, 0, info_bytes, true};
    manifest_text(t, j, generation, generation_bytes, profile, profile_bytes);
    if (!t.ok) { set_detail_text(j, fixed_names[0]); return Error::size; }
    j.info_size = t.size;
    hash::Sha256 h;
    h.reset(); h.update(j.info_out, j.info_size); h.finish(j.info_sha256);
    // Add the manifest and OS after the sorted content paths. The list omits its own digest.
    Text list = {j.list_out, j.builder.list_bytes, list_bytes, true};
    list_line(list, j.info_sha256, fixed_names[0]);
    list_line(list, j.ldr_sha256, fixed_names[2]);
    if (!list.ok) { set_detail_text(j, "files.sha256"); return Error::size; }
    j.list_size = list.size;
    h.reset(); h.update(j.list_out, j.list_size); h.finish(j.list_sha256);
    const U32 listed = count - fixed_outputs;
    for (U32 i = 0; i != listed; ++i) j.out_sizes[i] = j.builder.listed[i].package == none ? j.builder.catalog_bytes : 0;
    j.out_sizes[listed] = j.info_size; j.out_sizes[listed + 1] = j.list_size; j.out_sizes[listed + 2] = j.ldr_bytes;
    j.work = j.info_size + j.list_size;
    return Error::none;
}
