// Included by usb_service.cpp after job and file helpers. No install exemption or writer is used.
namespace bp = xy_forever::boot_partition_probe;
volatile U32 probe_on __attribute__((section(".data"))) = 0;
U32 probe_partition __attribute__((section(".data"))) = 0;
U32 probe_sizes[2] __attribute__((section(".data"))) = {};
bool boot_probe_active() { return probe_on != 0; }
U32 probe_result(const file_client::Reply& r) {
    return r.outcome ? file_client::status(r.outcome) : r.value ? U32(P::s_storage) : U32(P::s_ok);
}
void boot_probe_command(uint8_t seq, const uint8_t* payload, U32 length) {
    U32 code = P::s_bad_request, bytes = 8;
    const U32 op = length >= 4 ? P::get32(payload) : ~0u;
    if (!length || payload[0] > bp::end || (length >= 4 && op > bp::end)) {
        status_only(bp::command, seq, P::s_unsupported); return; // unlisted-type error has only its status
    }
    if (length >= 8 && P::get32(payload + 4) != bp::version) code = P::s_unsupported;
    else if (op == bp::begin && length == 8) {
        stop_idle_gc();
        if (probe_on || recovery_pending || job_running() || job_blocks(P::t_read_file) || uploading() || boot_sweep < 2) code = P::s_busy;
        else if (writes_due) { settle_begin(); code = P::s_busy; } // close the write window without blocking USB
        else if (mounted == file_client::mounted_stale) code = P::s_storage;
        else {
            // Op9 closes handles as well; it must answer before A can enter the probe.
            code = P::s_link;
            if (menu_link::usb_command(storage_server::command_item)) {
                read_enabled = 1;
                const file_client::Reply close = file_client::unmount();
                if (!close.outcome && (close.value == 1 || close.value == 3)) {
                    mounted = 0;
                    const file_client::Reply started = file_client::boot_probe(0);
                    if (!started.outcome && !started.error) probe_on = 1; // gate B even on a version mismatch
                    code = started.outcome ? file_client::status(started.outcome) :
                           started.error ? U32(P::s_busy) : started.value != int32_t(bp::version) ?
                           U32(P::s_unsupported) : U32(P::s_ok);
                    if (!code) {
                        probe_on = 1; probe_partition = 0; probe_sizes[0] = probe_sizes[1] = 0;
                        const file_client::Reply flags = file_client::boot_probe(1);
                        code = flags.outcome ? file_client::status(flags.outcome) : U32(P::s_ok);
                        if (!code) { P::put32(files + 8, U32(flags.value)); P::put32(files + 12, flags.error); bytes = 16; }
                    }
                }
            }
        }
    } else if (op == bp::end && length == 8 && probe_on) {
        code = probe_result(file_client::boot_select(0));
        if (!code) {
            probe_partition = 0;
            code = probe_result(file_client::boot_probe(2));
            if (!code) { probe_on = 0; mounted = 0; }
        }
    } else if ((op == bp::header && length == 12) || (op == bp::sector && length == 16)) {
        if (!probe_on) code = P::s_state;
        else {
            const U32 part = P::get32(payload + 8), at = op == bp::header ? 0 : P::get32(payload + 12);
            if (part < 1 || part > 2 || (op == bp::sector && (!at || !probe_sizes[part - 1] ||
                at > (probe_sizes[part - 1] + 511u) / 512u))) code = P::s_range;
            else {
                code = P::s_ok;
                if (probe_partition != part) {
                    code = probe_result(file_client::boot_select(part));
                    probe_partition = code ? 0 : part;
                }
                if (!code) code = probe_result(file_client::boot_sector(at, files + 8));
                if (!code) {
                    bytes = 520;
                    if (!at) {
                        const U32 size = P::get32(files + 12);
                        const bool valid = P::get32(files + 8) >= 2 && size && size <= bp::max_bytes &&
                                           files[20] && files[21] <= 1;
                        probe_sizes[part - 1] = valid ? size : 0;
                    }
                }
            }
        }
    }
    P::put32(files, code); P::put32(files + 4, bp::version);
    result.last_status = code;
    send(bp::command, seq, files, bytes);
}
