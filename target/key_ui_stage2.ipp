// Part of key_ui.cpp: the stage-2 services and run_stage2 (XYF_STAGE2 modes only), included in place.
#if defined(XYF_STAGE2)
// ---- Stage-2 services (ABI 1; called by stage 2 through the table) ----------
volatile uint32_t stage2_frame __attribute__((section(".data"))) = 0;
uint16_t* stage2_begin_frame() {
    Result& r = result;
    stage2_frame = 0;
    if (!dma_idle(100)) { ++r.present_skips; return 0; }
    wait_dma();
    if (byte(smc_dma_active)) {     // as present(): DMA idle without a done bit
        *reinterpret_cast<volatile uint8_t*>(smc_dma_active) = 0;
        r.present_skips += 0x10000u;
    }
    stage2_frame = 1;
    return reinterpret_cast<uint16_t*>(word(framebuffer));
}
void stage2_end_frame() {
    if (!stage2_frame) return;      // only after a successful begin_frame
    stage2_frame = 0;
    flush_framebuffer(word(framebuffer));
    transfer();
    if (byte(smc_dma_active)) ++result.presents;
}
uint32_t stage2_pop() {
    if (stage2_head == stage2_tail) return 0;
    return stage2_events[stage2_tail++ & (stage2_queue - 1)];
}
uint32_t stage2_next_event() {
    poll();                          // stock PSoC dispatch -> on_key -> queue
    ++result.polls;
    xy_forever::xyf_boot::tick();
    if (xy_forever::xyf_boot::link_lost()) return xyf::stage2::event_link_lost;
    if (const uint32_t event = stage2_pop()) return event;
    delay_us(5000);
    return 0;
}
uint32_t stage2_now_ms() { return ms_since(result.start_cycles); }
const xyf::stage2::Services stage2_services = {4, stage2_begin_frame, stage2_end_frame, stage2_next_event,
                                              stage2_now_ms};

#if defined(XYF_STAGE2_MENU)
// ABI 2 (main XY Forever, --xyf-stage2-menu): stage 2 runs the menu and
// returns requests (xyf::stage2::decide). Stage 1 performs each one with the
// same calls as its own menu, or answers with a note and calls stage 2 again
// (at most max_rounds times). True: the stage-1 menu (stage 2 asked for it,
// none is loaded, or too many rounds). False: boot now (default or entry, or
// the link was lost -> both built-in).
__attribute__((noinline)) bool run_stage2() {
    using namespace xyf::stage2;
    Result& r = result;
    const uint32_t entry = xy_forever::xyf_boot::stage2_entry();
    if (!entry) return true;
    Abi2 abi2;
    abi2.v1.abi = abi; abi2.v1.bytes = sizeof(abi2); abi2.v1.role = 1;
    abi2.v1.stage1_version = this_stage1;
    for (uint32_t i = 0; i != 3; ++i) abi2.v1.key_bitmap[i] = r.bitmap[i];
    abi2.v1.services = &stage2_services;
    abi2.note = 0;
    stage2_head = stage2_tail = 0;
    stage2_active = 1;
    const uint32_t start = ms_since(r.start_cycles);
    Do next = Do::menu;
    uint32_t chosen = 0;
    for (uint32_t round = 0; round != max_rounds; ++round) {
        uint32_t blob = 0;
        abi2.catalog = xy_forever::xyf_boot::catalog_blob(blob);
        abi2.catalog_bytes = blob;
        abi2.features = abi2.catalog ? uint32_t(feature_catalog) : 0u;
#if defined(XYF_STATE_WRITE)
        abi2.features |= feature_set_default;
#endif
        const uint32_t exit = xyf_stage2_enter(entry, &abi2.v1);
        xy_forever::xyf_boot::stage2_done(exit, ms_since(r.start_cycles) - start);
        const xyf::catalog::Catalog* catalog = xy_forever::xyf_boot::catalog();
        next = decide(exit, xy_forever::xyf_boot::link_lost(), catalog ? catalog->count : 0u, abi2.features,
                      chosen);
        if (next == Do::note_unavailable) { abi2.note = "Not available in this build."; continue; }
        if (next == Do::note_no_entry) { abi2.note = "No such library entry."; continue; }
#if defined(XYF_STATE_WRITE)
        if (next == Do::set_default) {                 // the only write path (a writes the state slot)
            abi2.note = xy_forever::xyf_boot::set_default(chosen);
            if (xy_forever::xyf_boot::link_lost()) { next = Do::boot_builtin; break; }
            continue;
        }
#endif
        break;
    }
    stage2_active = 0;
    dirty = 1;
    if (next == Do::boot_builtin) { r.exit_reason = exit_link; return false; }
    if (next == Do::boot_default) { xy_forever::xyf_boot::choose_default(); r.exit_reason = exit_play; return false; }
    if (next == Do::boot_entry) { xy_forever::xyf_boot::choose(chosen); r.exit_reason = exit_play; return false; }
    return true;                                       // menu (also mtp/set_default when not built in)
}
#else
// Runs a loaded stage 2 once. True: continue into the stage-1 menu (no stage
// 2, or it asked for the menu). False: boot now (exit_continue -> the default,
// or the link was lost -> both built-in).
__attribute__((noinline)) bool run_stage2() {
    Result& r = result;
    const uint32_t entry = xy_forever::xyf_boot::stage2_entry();
    if (!entry) return true;
    xyf::stage2::Abi abi;
    abi.abi = xyf::stage2::abi; abi.bytes = sizeof(abi); abi.role = 1;
    abi.stage1_version = xyf::stage2::this_stage1;
    for (uint32_t i = 0; i != 3; ++i) abi.key_bitmap[i] = r.bitmap[i];
    abi.services = &stage2_services;
    stage2_head = stage2_tail = 0;
    stage2_active = 1;
    const uint32_t start = ms_since(r.start_cycles);
    const uint32_t exit = xyf_stage2_enter(entry, &abi);
    stage2_active = 0;
    xy_forever::xyf_boot::stage2_done(exit, ms_since(r.start_cycles) - start);
    dirty = 1;
    const xyf::stage2::Next next = xyf::stage2::after(exit, xy_forever::xyf_boot::link_lost());
    if (next == xyf::stage2::Next::menu) return true;
    if (next == xyf::stage2::Next::boot_builtin) { r.exit_reason = exit_link; return false; }
    xy_forever::xyf_boot::choose_default();
    r.exit_reason = exit_play;
    return false;
}
#endif
#endif
