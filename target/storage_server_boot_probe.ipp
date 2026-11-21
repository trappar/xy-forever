// Included by storage_server.cpp. #7 uses only the existing admitted stock executors.
#if defined(XYF_USB_SERVICE)
U32 probe_menu __attribute__((section(".data"))) = 0;
U32 probe_resume __attribute__((section(".data"))) = 0;
U32 probe_zero[128] __attribute__((section(".data"), aligned(32))) = {};
// Keep the cache flush out of the inlined dispatcher for the scoped-call audit.
__attribute__((noinline)) void probe_zero_body(U32 request) {
    const U32 address = reinterpret_cast<U32>(probe_zero);
    stock<0x889f4f10u, void>(address, 512u);
    word(request + 0x20) = address;
    word(request + 0x24) = 512u;
}
bool boot_probe_query(U32 request, Stock121PeerSession& session) {
    const U32 argument = word(request + 8);
    if (word(request) != path_class || byte(request + 5) != 0x21u ||
        (argument & ~0xffu) != boot_partition_probe::tag) return false;
    const U32 op = argument & 0xffu;
    const bool writable =
#if defined(XYF_MTP) || defined(XYF_USB_FILE_WRITE)
        window_open != 0;
#else
        false;
#endif
    if (!op && !probe_mode && !boot_selected() && !writable && !held_files()) {
        // Fixed DDR data addresses from #7. Never OTP, MMRs, or instruction SRAM.
        probe_menu = word(0x88a1f3c4u); probe_resume = byte(0x88a291c0u);
        probe_mode = 1;
        session.reply(request, boot_partition_probe::version, 0);
    } else if (op == 1 && probe_mode) session.reply(request, probe_menu, probe_resume);
    else if (op == 2 && probe_mode && !selected_partition && !select_attempted) {
        probe_mode = 0; session.reply(request, 0, 0);
    } else session.reply(request, 0xffffffffu, error_busy);
    return true;
}
bool serve_boot_probe(U32 request, Stock121PeerSession& session) {
    const U32 cls = word(request), op = byte(request + 5), argument = word(request + 8);
    const boot_partition_probe::Action action = boot_partition_probe::action(cls, op, argument, selected_partition);
    if (action == boot_partition_probe::select && word(cls + 0x14) == 0x889f76cau) {
        if (argument) select_attempted = 1;
        stock<0x889f76cau, void>(request);
        // A failed select can already have changed the hardware partition.
        if (!word(request + 0x10)) { selected_partition = argument; if (!argument) select_attempted = 0; }
        else selected_partition = 0;
        return true;
    }
    if (action == boot_partition_probe::read && word(cls + 0x14) == 0x889f6b7cu) {
        stock<0x889f6b7cu, void>(request); return true;
    }
    if (cls == partition_class || cls == write_class || (cls == sector_class && op != 0x2fu)) {
        if (cls == sector_class && op == 0x11u) {
            // The one-sector request always has its 512-byte body, even on refusal.
            probe_zero_body(request);
        }
        session.reply(request, 0xffffffffu, error_access);
        ++result.refused;
        return true;
    }
    return false; // file engine refuses all file operations while probe_mode holds its partition gate
}
#else
bool boot_probe_query(U32, Stock121PeerSession&) { return false; }
#endif
