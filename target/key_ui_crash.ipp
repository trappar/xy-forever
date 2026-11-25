// Part of key_ui.cpp: B's crash screen (#102), included in place (in key_ui's anonymous namespace).
// While B's menu has the screen (crash_on after its first frame; crash_off before the menu ends, so
// before the boot hand-off, and before B resets itself, usb_service::reset_self), the exception vector
// EVT3 points at xyf_crash_vector instead of the vector the stock code left there (saved, put back).
// Nothing else changes: EVT5 (hardware errors; masked at cold time, IMASK 0x881f), the NMI and the
// interrupt vectors, IMASK, the watchdog. The stock 1.2.1 code holds no EXCPT instruction (each EXCPT the
// listing decodes lies in a data table) and installs all its CPLB descriptors at start (14 data, 9
// instruction; nothing is left to load on a miss), so every exception here is a fault.
//   xyf_crash_vector (exception level): the first time, records SEQSTAT, RETX and RETS in crash_record,
//     then returns from the exception into xyf_crash_run (RETX); a second exception (one while the crash
//     screen draws) stops there in an IDLE loop. It touches nothing but crash_record.
//   xyf_crash_run (the level that faulted): all maskable interrupts off (CLI), SP back to the top of
//     XY Forever's stack, draws the crash screen with the menu's own present (stock display calls, CRC-checked
//     before the menu showed its first frame), then stops in an IDLE loop until a power cycle. No storage,
//     no reset, no link: A ends its session when B goes silent (it closes its read-write window itself).
// Built with the USB service (the product builds); the narrow offline modes have no room for it (no vector).
#if defined(XYF_USB_SERVICE)
const uint32_t evt3 = 0x1fc0200cu;      // core event vector table + 3 * 4 (stock 88a33b86: base 0x1fc02000)
volatile xyf::crash::Record crash_record __asm__("xyf_crash_record")
    __attribute__((section(".data"), used)) = {0, 0, 0, 0};
volatile uint32_t crash_stack __asm__("xyf_crash_stack") __attribute__((section(".data"), used)) = stack_top;
volatile uint32_t crash_saved __attribute__((section(".data"))) = 0;   // EVT3 before crash_on
volatile uint32_t crash_armed __attribute__((section(".data"))) = 0;
volatile uint32_t crash_shown __attribute__((section(".data"))) = 0;   // draw_screen draws the crash screen
xyf::crash::Text crash_text __attribute__((section(".data"))) = {{0}, {0}, {0}, {0}};

__asm__(".text\n"
        ".align 2\n"
        ".global xyf_crash_vector\n"
        ".type xyf_crash_vector, STT_FUNC\n"
        "xyf_crash_vector:\n"
        "    P0.H = xyf_crash_record;\n"
        "    P0.L = xyf_crash_record;\n"
        "    R0 = [P0];\n"
        "    CC = R0 == 0;\n"
        "    IF !CC JUMP 2f;\n"
        "    R0 = SEQSTAT;\n"
        "    [P0 + 4] = R0;\n"
        "    R0 = RETX;\n"
        "    [P0 + 8] = R0;\n"
        "    R0 = RETS;\n"
        "    [P0 + 12] = R0;\n"
        "    R0 = 1;\n"
        "    [P0] = R0;\n"
        "    P0.H = xyf_crash_run;\n"
        "    P0.L = xyf_crash_run;\n"
        "    RETX = P0;\n"
        "    RTX;\n"
        "2:\n"
        "    IDLE;\n"
        "    JUMP 2b;\n"
        ".size xyf_crash_vector, .-xyf_crash_vector\n"
        ".align 2\n"
        ".type xyf_crash_run, STT_FUNC\n"
        "xyf_crash_run:\n"
        "    CLI R0;\n"
        "    P0.H = xyf_crash_stack;\n"
        "    P0.L = xyf_crash_stack;\n"
        "    P1 = [P0];\n"
        "    SP = P1;\n"
        "    FP = SP;\n"
        "    SP += -12;\n"
        "    CALL xyf_crash_show;\n"
        "3:\n"
        "    IDLE;\n"
        "    JUMP 3b;\n"
        ".size xyf_crash_run, .-xyf_crash_run\n");

void present();
// From xyf_crash_run: the crash screen through the menu's present (a frame still going out: up to ten
// tries of 100 ms each). Returns to the IDLE loop either way.
void crash_show() __asm__("xyf_crash_show");
__attribute__((used, noinline)) void crash_show() {
    const xyf::crash::Record r = {crash_record.taken, crash_record.seqstat, crash_record.retx, crash_record.rets};
    xyf::crash::describe(r, where_word_now, crash_text);
    crash_shown = 1;
    for (uint32_t i = 0; i != 10; ++i) {
        const uint32_t sent = result.presents;
        present();
        if (result.presents != sent) return;
    }
}
// ---- #314: the stop screen ------------------------------------------------------------------------------------
// B has stopped as a whole during long reads with a host polling: the screen kept its last frame, no input, no crash
// screen, USB gone, nothing left to read afterwards. A stock assert does that: it ends in the ROM's IDLE loop
// (0803dfcc) at the level that asserted, and the stock interrupt wrapper (11a015d8) has pushed RETI by then, so
// a higher level still gets in. The core timer (IVG6, above every peripheral; unused by the stock code: no
// 0x1fc03000 in the 1.2.1 listing) is that level, as a dead man's timer: while the menu loop runs it counts
// stop_ms down, and every pass of the loop and every poll of a bounded wait winds it back (hang_kick, at most
// every 100 ms), so it never interrupts a loop that moves. (An earlier tick every 0.5 s seemed to change how
// often B froze.) Its one interrupt on purpose is the proof, 1 ms after the menu loop starts: only once that came
// does a beat wind the timer back (hang_live), and the trail shows T (t: it never came, so this timer cannot tell
// anything).
// When it does interrupt, nothing has moved for stop_ms: it logs the interrupted instruction (RETI), RETS, FP and
// IPEND (StallLog, diag.status block STL1), draws the stop screen with the menu's own present (crash::Hang: also
// the callers, the message a stock assert left at the frame, the wait, the loop's last step) and returns. A B that
// really stopped keeps that screen (drawn again every stop_ms); a loop that goes on after all draws over it at its
// next pass, and STATUS still has the entry. No reset, no storage, no link. An interrupt although the loop moved
// under half of stop_ms ago means winding does not work on this timer: it is turned off for good (the trail shows
// x). hang_hold (usb_service: the stock boot writer, a long stock call) stops the timer for its length.
// No stop screen after a freeze: the core itself hung (anomaly 19000058), interrupts were masked (CLI, or a
// handler that never pushed RETI), or the display's DMA no longer ends.
const uint32_t evt6 = 0x1fc02018u, ipend = 0x1fc02108u;
const uint32_t tcntl = 0x1fc03000u, tperiod = 0x1fc03004u, tscale = 0x1fc03008u, tcount = 0x1fc0300cu;
const uint32_t tmpwr = 1, tmren = 2, tautorld = 4, tint = 8, proof_cycles = 400000u;   // 1 ms at 400 MHz
const uint32_t ivg6 = 0x40u;
static_assert(tcntl == hang_tcntl && tcount == hang_tcount && (tmpwr | tmren | tautorld) == 7u, "hang_wind");
static_assert(hang_cycles / 400000u == xyf::crash::stop_ms, "the dead man's timer");
volatile uint32_t& mmr(uint32_t address) { return *reinterpret_cast<volatile uint32_t*>(address); }
volatile uint32_t hang_armed __attribute__((section(".data"))) = 0;
volatile uint32_t hang_saved __attribute__((section(".data"))) = 0;   // EVT6 before
volatile uint32_t hang_shown __attribute__((section(".data"))) = 0;   // draw_screen draws the stop screen
xyf::crash::Hang hang_record __attribute__((section(".data"))) = {0, 0, 0, 0, 0, 0, {0}, {0}};
xyf::crash::HangText hang_text __attribute__((section(".data"))) = {{0}, {0}, {0}, {0}};

__asm__(".text\n"
        ".align 2\n"
        ".global xyf_hang_vector\n"
        ".type xyf_hang_vector, STT_FUNC\n"
        "xyf_hang_vector:\n"
        "    [--SP] = ASTAT;\n"
        "    [--SP] = RETS;\n"
        "    [--SP] = (R7:0, P5:0);\n"
        "    [--SP] = I0; [--SP] = I1; [--SP] = I2; [--SP] = I3;\n"
        "    [--SP] = M0; [--SP] = M1; [--SP] = M2; [--SP] = M3;\n"
        "    [--SP] = B0; [--SP] = B1; [--SP] = B2; [--SP] = B3;\n"
        "    [--SP] = L0; [--SP] = L1; [--SP] = L2; [--SP] = L3;\n"
        "    [--SP] = A0.X; [--SP] = A0.W; [--SP] = A1.X; [--SP] = A1.W;\n"
        "    [--SP] = LC0; [--SP] = LT0; [--SP] = LB0; [--SP] = LC1; [--SP] = LT1; [--SP] = LB1;\n"
        "    L0 = 0; L1 = 0; L2 = 0; L3 = 0;\n"
        "    R0 = RETI;\n"
        "    R1 = FP;\n"
        "    R2 = RETS;\n"
        "    SP += -12;\n"
        "    CALL xyf_hang_tick;\n"
        "    SP += 12;\n"
        "    LB1 = [SP++]; LT1 = [SP++]; LC1 = [SP++]; LB0 = [SP++]; LT0 = [SP++]; LC0 = [SP++];\n"
        "    A1.W = [SP++]; A1.X = [SP++]; A0.W = [SP++]; A0.X = [SP++];\n"
        "    L3 = [SP++]; L2 = [SP++]; L1 = [SP++]; L0 = [SP++];\n"
        "    B3 = [SP++]; B2 = [SP++]; B1 = [SP++]; B0 = [SP++];\n"
        "    M3 = [SP++]; M2 = [SP++]; M1 = [SP++]; M0 = [SP++];\n"
        "    I3 = [SP++]; I2 = [SP++]; I1 = [SP++]; I0 = [SP++];\n"
        "    (R7:0, P5:0) = [SP++];\n"
        "    RETS = [SP++];\n"
        "    ASTAT = [SP++];\n"
        "    RTI;\n"
        ".size xyf_hang_vector, .-xyf_hang_vector\n");
extern "C" void xyf_hang_vector() __asm__("xyf_hang_vector");
void hang_disarm() {
    if (!hang_armed) return;
    uint32_t m;
    __asm__ volatile("CLI %0;" : "=d"(m) : : "memory");
    m &= ~ivg6;
    mmr(tcntl) = 0;
    hang_live = 0;
    *reinterpret_cast<volatile uint32_t*>(evt6) = hang_saved;
    __asm__ volatile("CSYNC;" : : : "memory");
    hang_armed = 0;
    __asm__ volatile("STI %0;" : : "d"(m) : "memory");
}
// From the menu loop's start to crash_off: the stage-2 menu before it has a loop of its own.
void hang_arm() {
    using namespace xy_forever::key_ui;
    if (hang_armed || !where_screen) return;        // where_screen: the menu's present works
    hang_live = hang_held = hang_shown = 0;
    stall_log.timer = stall_armed;
    hang_at = cycles_div1024();
    hang_saved = word(evt6);
    *reinterpret_cast<volatile uint32_t*>(evt6) = reinterpret_cast<uint32_t>(&xyf_hang_vector);
    mmr(tcntl) = tmpwr;
    mmr(tscale) = 0;
    mmr(tperiod) = hang_cycles;
    mmr(tcount) = proof_cycles;                     // the proof: one interrupt, 1 ms from here
    __asm__ volatile("CSYNC;" : : : "memory");
    mmr(tcntl) = tmpwr | tmren | tautorld;
    hang_armed = 1;
    uint32_t m;
    __asm__ volatile("CLI %0;" : "=d"(m) : : "memory");
    __asm__ volatile("STI %0;" : : "d"(m | ivg6) : "memory");
}
// A readable byte for the stop screen's message (crash::copy_text): the stock image and data, L1 data.
// (A type, not a function: its call stays direct for the call audit.)
struct HangByte {
    bool operator()(uint32_t a, int& c) const {
        if (!((a >= 0x88000000u && a < 0x88b00000u) || (a >= 0x11900000u && a < 0x11910000u) ||
              (a >= 0x11b00000u && a < 0x11b02000u)))
            return false;
        c = *reinterpret_cast<const volatile uint8_t*>(a);
        return true;
    }
};
bool hang_word(uint32_t a, uint32_t& v) {
    if ((a & 3u) || a < 0x80000000u || a >= 0x90000000u) return false;    // SDRAM (B's stack)
    v = word(a);
    return true;
}
extern "C" __attribute__((used, noinline)) void xyf_hang_tick(uint32_t reti, uint32_t fp,
                                                              uint32_t rets) __asm__("xyf_hang_tick");
extern "C" __attribute__((used, noinline)) void xyf_hang_tick(uint32_t reti, uint32_t fp, uint32_t rets) {
    using namespace xy_forever::key_ui;
    // The sticky TINT: cleared by writing 0 (BF5xx) or 1 (W1C); whichever leaves it clear.
    mmr(tcntl) = tmpwr | tmren | tautorld;
    if (mmr(tcntl) & tint) mmr(tcntl) = tmpwr | tmren | tautorld | tint;
    StallLog& l = stall_log;
    if (!hang_live) {                               // the proof: from here a beat winds the timer back
        if (l.timer & (stall_proven | stall_off)) {   // not expected (stopped or off): never again
            mmr(tcntl) = 0;
            return;
        }
        l.timer = l.timer | stall_proven;
        hang_at = hang_wound = cycles_div1024();
        hang_live = 1;                              // TCOUNT is TPERIOD again (TAUTORLD): stop_ms from here
        return;
    }
    const uint32_t quiet = ms_since(hang_at);
    if (quiet < xyf::crash::stop_ms / 2) {          // the loop moved since: this timer is not wound back. Off.
        mmr(tcntl) = 0;
        hang_live = 0;
        l.timer = l.timer | stall_off;
        return;
    }
    if (hang_shown) {                               // the same stop, stop_ms later: only its screen again
        present();
        return;
    }
    const uint32_t active = word(ipend) & 0xffffu;
    StallEntry& e = l.entry[l.count++ & (stall_entries - 1)];
    e.ms = ms_since(result.start_cycles);
    e.pc = reti;
    e.rets = rets;
    e.fp = fp;
    e.state = active | (quiet < 0xffffu ? quiet : 0xffffu) << 16;
    xyf::crash::Hang& h = hang_record;
    h.ipend = active;
    h.pc = reti;
    h.rets = rets;
    h.fp = fp;
    h.caller = h.caller2 = 0;
    uint32_t above = 0, text = 0;
    hang_word(fp + 4, h.caller);
    if (hang_word(fp, above)) hang_word(above + 4, h.caller2);
    h.text[0] = 0;
    if (hang_word(fp - 4, text) && !xyf::crash::copy_text(h.text, sizeof(h.text), text, HangByte())) h.text[0] = 0;
    xy_forever::usb_service::crumb(h.crumb, sizeof(h.crumb));
    xyf::crash::hang_describe(h, where_word_now, hang_text);
    hang_shown = 1;
    for (uint32_t i = 0; i != 10; ++i) {            // a frame still going out: up to ten tries of 100 ms each
        const uint32_t sent = result.presents;
        present();
        if (result.presents != sent) break;
    }
}
// usb_service's long stock call (the boot writer): the timer stands still for it, then counts from the top.
void hang_pause(bool on) {
    hang_held = on ? 1u : 0u;
    if (!hang_live) return;
    if (on) {
        mmr(tcntl) = tmpwr;
        return;
    }
    hang_at = hang_wound = cycles_div1024();
    hang_wind();
}

void crash_on() {
    where_screen = 1;                               // key_ui_where.ipp: a long wait may paint its code from here
    if (crash_armed) return;
    crash_record.taken = 0;
    crash_saved = word(evt3);                       // the stock code reads it too (88a33b98)
    *reinterpret_cast<volatile uint32_t*>(evt3) = reinterpret_cast<uint32_t>(&xyf_crash_vector);
    __asm__ volatile("CSYNC;" : : : "memory");
    crash_armed = 1;
}
void crash_off() {
    where_screen = 0;
    hang_disarm();
    if (!crash_armed) return;
    *reinterpret_cast<volatile uint32_t*>(evt3) = crash_saved;
    __asm__ volatile("CSYNC;" : : : "memory");
    crash_armed = 0;
}
#if defined(XYF_CRASH_TRIGGER)
// Test build xyf-crash-trigger only (#102; xyf_build.py refuses it in any other mode): one chosen fault from
// the menu loop (home_pump, diag.crash) while the vector above is armed. R0 = kind: 1 EXCPT 1 (EXCAUSE 0x01,
// RETX the next instruction); 3 the undefined opcode 0x0001 (0x21); 4 a read of 0x90000000 (0x26: just above
// the 256 MiB of SDRAM, in no stock data CPLB: table 0x0803c0b4, docs/hardware/boot.md). No kind 2: misaligned
// reads do not fault on B (docs/hardware/boot.md). Reads only, no store. Returns for another kind.
__asm__(".text\n"
        ".align 2\n"
        ".global xyf_crash_fault\n"
        ".type xyf_crash_fault, STT_FUNC\n"
        "xyf_crash_fault:\n"
        "    R1 = 1;\n"
        "    CC = R0 == R1;\n"
        "    IF CC JUMP xyf_crash_at_excpt;\n"
        "    R1 = 3;\n"
        "    CC = R0 == R1;\n"
        "    IF CC JUMP xyf_crash_at_illegal;\n"
        "    R1 = 4;\n"
        "    CC = R0 == R1;\n"
        "    IF CC JUMP xyf_crash_unmapped;\n"
        "    RTS;\n"
        "xyf_crash_at_excpt:\n"
        "    EXCPT 1;\n"
        "    RTS;\n"
        "xyf_crash_at_illegal:\n"
        "    .short 0x0001\n"
        "    RTS;\n"
        "xyf_crash_unmapped:\n"
        "    P0.H = 0x9000;\n"
        "    P0.L = 0;\n"
        "xyf_crash_at_unmapped:\n"
        "    R0 = [P0];\n"
        "    RTS;\n"
        ".size xyf_crash_fault, .-xyf_crash_fault\n");
extern "C" void xyf_crash_fault(uint32_t kind) __asm__("xyf_crash_fault");
#endif
#else
const uint32_t crash_shown = 0, hang_shown = 0;
const xyf::crash::Text crash_text = {{0}, {0}, {0}, {0}};
const xyf::crash::Hang hang_record = {0, 0, 0, 0, 0, 0, {0}, {0}};
const xyf::crash::HangText hang_text = {{0}, {0}, {0}, {0}};
void crash_on() {}
void crash_off() {}
#endif
