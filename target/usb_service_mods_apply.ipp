// Part of usb_service.cpp, included there at this point: not compiled alone.
// MODS_APPLY: base NUL profile NUL generation NUL machines NUL extensions NUL name NUL u32 flags +
// SHA-256 of every byte before it. Reply: u32 status, u32 job id, u32 missing input. mods_begin is the
// start itself (the menu's Mods page starts the job through it too).
U32 mods_begin(const uint8_t* payload, U32 length, U32& id, U32& missing, U32& taken) {
    stop_idle_gc();
    JobSlot& s = slot();
    const char* field[6];
    const U32 most[6] = {31, 31, 31, 1023, 255, 47};   // 64 machines
    U32 at = 0, code = P::s_ok, machines = 0, extensions = 0;
    id = 0; missing = 0; taken = 0;
    for (U32 k = 0; k != 6 && code == P::s_ok; ++k) {
        const U32 begin = at;
        while (at < length && payload[at]) ++at;
        if (at >= length || at - begin > most[k]) code = P::s_bad_request;
        field[k] = reinterpret_cast<const char*>(payload + begin);
        ++at;
    }
    U32 flags = 0;
    if (code == P::s_ok && at + 4 + 32 != length) code = P::s_bad_request;
    if (code == P::s_ok) {
        flags = P::get32(payload + at);
        U32 t = 0;                                                       // the lists and name into the slot
        for (U32 k = 3; k != 6; ++k) { const char* f = field[k]; do s.text[t++] = *f; while (*f++); }
        char* list_m = s.text;
        char* list_e = list_m + text_bytes(list_m) + 1;
        const char* name = list_e + text_bytes(list_e) + 1;
        const U32 name_bytes = text_bytes(name);
        bool name_ok = name_bytes && name[0] != ' ' && name[name_bytes - 1] != ' ';
        for (U32 i = 0; i != name_bytes; ++i) if (name[i] < 0x20 || name[i] > 0x7e) name_ok = false;
        if (flags > 3 || !xyf::store::valid_id(field[0]) || !job_token(field[1], true) || !job_token(field[2], true) ||
            !name_ok || !job_ids_split(list_m, s.machines, max_job_machines, machines) || !machines ||
            !job_ids_split(list_e, s.extensions, max_job_extensions, extensions))
            code = P::s_bad_request;
        else {
            s.input.name = name;
            xyf_core::hash::Sha256 h;
            xyf_core::hash::Digest d;
            if (!h.update(payload, at + 4) || !h.finish(d) || !sha_equal(d.bytes, payload + at + 4)) code = P::s_sha;
        }
    }
    if (code == P::s_ok && uploading()) code = P::s_busy;                                      // an upload
    if (code == P::s_ok && !xy_forever_cold_policy) code = P::s_state;
    if (code == P::s_ok && (!join(s.base, job_firmware, field[0]) || !join(s.stock, s.base, "/os.ldr") ||
                            !join(s.official, s.base, "/content.sha256") ||
                            !join(s.generation, job_mods, field[2], "")))   // mods/<generation>/ (#124)
        code = P::s_bad_request;
    if (code == P::s_ok && !a_serving()) code = P::s_link;
    if (code == P::s_ok) code = file_client::mount_ready(mounted);
    if (code == P::s_ok) { code = job_target(s); taken = code == P::s_state; }   // the library id it takes
    // Read-only availability preflight. The job selects and verifies the actual metadata record.
    char probe[file_engine::max_path];
    const char* catalog = job_catalog;
#if defined(XYF_CONTENT_SWITCH)
    if (code == P::s_ok) catalog = content_job_catalog();               // the saved copy while mods are in
#endif
    const char* inputs[4] = {s.stock, s.official, catalog, probe};
    join(probe, s.generation, "/base/", xyf::package_record::native_name);
    for (U32 k = 0; k != 4 && code == P::s_ok; ++k) {
        uint8_t record[file_client::record_bytes];
        file_client::Reply r = file_client::stat(inputs[k], record);
        if (k == 3 && !r.outcome && r.value != 0 && r.error == file_client::enoent) {
            join(probe, s.generation, "/base/", xyf::package_record::legacy_name);
            r = file_client::stat(probe, record);
        }
        if (r.outcome) { if (r.outcome == file_client::link) mounted = file_client::mounted_stale; code = file_client::status(r.outcome); }
        else if (r.value != 0) {
            code = k == 3 ? file_client::change_status(r.error) : uint32_t(P::s_not_found);
            if (code == P::s_not_found) missing = k + 1;
        }
    }
    if (code == P::s_ok) {                                               // the store's version (#124, missing 5)
        uint8_t text[16];
        uint32_t bytes = 0, version = 0;
        join(probe, s.generation, "/", xyf::mods_store::file);
        code = file_client::read_file(probe, 0, true, text, sizeof(text), bytes, mounted);
        if (code == P::s_ok) version = xyf::mods_store::version_of(text, bytes);
        if (code == P::s_not_found || (code == P::s_ok && version != xyf::mods_store::version)) {
            code = version > xyf::mods_store::version ? P::s_unsupported : P::s_not_found;   // newer; older or unreadable
            missing = 5;
        }
    }
    // A job id whose output folder does not exist yet.
    for (U32 tries = 0; code == P::s_ok; ++tries) {
        char number[12];
        if (tries == job_ids) { code = P::s_state; break; }
        id = ++job_counter;
        decimal_text(number, id);
        join(s.out, job_tmp, "/job-", number);
        join(s.old, s.out, "-old");                                      // the old entry's place
        uint8_t record[file_client::record_bytes];
        file_client::Reply r = file_client::stat(s.out, record);
        if (!r.outcome && r.value != 0) r = file_client::stat(s.old, record);
        if (r.outcome) { if (r.outcome == file_client::link) mounted = file_client::mounted_stale; code = file_client::status(r.outcome); }
        else if (r.value != 0) break;
    }
    if (code == P::s_ok && (code = enter_writes()) == P::s_ok) {
        storage_used = 1;
        writes_due = 1; writes_ms = now();                               // closed when the job ends
        uint32_t error = 0;
        code = file_client::change(0x26u, job_tmp, error, mounted);       // mkdir tmp (it may be there)
        if (code != P::s_ok && code != P::s_link && error == file_client::eexist) code = P::s_ok;
        if (code == P::s_ok) code = file_client::change(0x26u, s.out, error, mounted);
    }
    if (code == P::s_ok) {
        s.input.selection.machines = s.machines; s.input.selection.machine_count = machines;
        s.input.selection.extensions = s.extensions; s.input.selection.extension_count = extensions;
        s.input.selection.layout = 0; s.input.selection.layout_count = 0; s.input.selection.has_layout = false;
        s.input.selection.expanded = (flags & 2u) != 0;                      // bit 1, the 4 MiB layout
        s.input.generation = s.generation; s.input.stock = s.stock; s.input.catalog = catalog;
        s.input.official = s.official; s.input.out = s.out; s.input.policy = xy_forever_cold_policy;
        s.input.profiling = false; s.input.unverified = (flags & 1u) != 0; s.input.flat = true;
        s.metadata_scan.folder = 0;
        s.calls.context = &s; s.calls.probe_metadata = job_probe_metadata;
        s.calls.size = job_size; s.calls.open = job_open; s.calls.read = job_read;
        s.calls.write = job_write; s.calls.close = job_close; s.calls.rename = job_rename; s.calls.remove = job_remove;
        s.calls.mkdir = job_mkdir;
        s.id = id; s.started_ms = s.ended_ms = now(); s.longest_ms = 0; s.link = 0; s.debris = 0;
        s.said_ms = s.started_ms; s.said_phase = ~0u;
        s.stage = js_sweep; s.had_old = s.commit_error = s.commit_code = s.extra_steps = s.clean_calls = 0;
        sweep_begin(s.leftovers, s.out);                                     // leftovers, never this output
        job_kind = 0; job_usb = 0;                                       // mods_apply sets it
        job_state = job_busy;
        if (save_active) save_lap(xl::p_job_base);                       // a Save's build, else a USB one
        else {
            xl::begin(save_times, xl::k_build, now(), xl::p_job_base);
            save_times.mods = machines + extensions; save_times.expanded = (flags & 2u) ? 1u : 0u;
        }
        xyf_core::Byte* region = reinterpret_cast<xyf_core::Byte*>(XYF_JOB_REGION_BEGIN + job_slot_bytes);
        if (mj::start(s.job, s.input, s.calls, region, job_work_end - XYF_JOB_REGION_BEGIN - job_slot_bytes) !=
            mj::Error::none)
            job_finish(s);                                               // refused: failed at once (job.status)
        else job_say(s, true);
    }
    if (code != P::s_ok) id = 0;
    return code;
}
void mods_apply(uint8_t seq, const uint8_t* payload, U32 length) {
    U32 id = 0, missing = 0, taken = 0;
    const U32 code = mods_begin(payload, length, id, missing, taken);
    if (code == P::s_ok && job_running()) job_usb = 1;                  // the menu names it
    uint8_t p[12]; P::put32(p, code); P::put32(p + 4, id); P::put32(p + 8, missing);
    result.last_status = code;
    send(P::t_mods_apply, seq, p, 12);
}
