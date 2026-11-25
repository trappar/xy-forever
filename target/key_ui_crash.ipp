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
// ---- #314: the hang screen -----------------------------------------------------------------------------------
// B has stopped as a whole during long reads with a host polling: the screen kept its last frame, no input, no
// crash screen, USB gone. A processor stuck inside an interrupt handler (IVG11, where the SEC's peripheral
// interrupts arrive: a stock assert ends in the ROM's IDLE loop, 0803dfcc, at the level that asserted) looks like
// that and leaves no record. So while the menu has the screen, the core timer (unused by the stock code: no
// 0x1fc03000 in the 1.2.1 listing) interrupts at IVG6, above every peripheral, every 0.5 s. Its tick does nothing
// while the menu loop moves (hang_beat: each pass and each bounded wait's poll) or no level 7..14 is active
// (IPEND); after hang_ticks such ticks in a row it records where B is (crash::Hang) and draws the hang screen with
// the menu's own present, all interrupts off (as the crash screen), then stops in an IDLE loop. No reset, no
// storage, no link: like the crash screen, it only shows what already stopped. hang_hold (usb_service: the stock
// boot writer, a long stock call with its own interrupts) keeps it from looking.
const uint32_t evt6 = 0x1fc02018u, ipend = 0x1fc02108u;
const uint32_t tcntl = 0x1fc03000u, tperiod = 0x1fc03004u, tscale = 0x1fc03008u, tcount = 0x1fc0300cu;
const uint32_t tmpwr = 1, tmren = 2, tautorld = 4, tint = 8, tick_cycles = 200000000u;   // 0.5 s at 400 MHz
const uint32_t ivg6 = 0x40u, levels_7_14 = 0x7f80u;
volatile uint32_t& mmr(uint32_t address) { return *reinterpret_cast<volatile uint32_t*>(address); }
volatile uint32_t hang_armed __attribute__((section(".data"))) = 0;
volatile uint32_t hang_saved __attribute__((section(".data"))) = 0;   // EVT6 before
volatile uint32_t hang_seen __attribute__((section(".data"))) = 0;    // hang_beat at the last tick
volatile uint32_t hang_count __attribute__((section(".data"))) = 0;   // ticks in a row that found B stuck
volatile uint32_t hang_last __attribute__((section(".data"))) = 0;    // cycles_div1024 at the last tick
volatile uint32_t hang_shown __attribute__((section(".data"))) = 0;   // draw_screen draws the hang screen
xyf::crash::Hang hang_record __attribute__((section(".data"))) = {0, 0, 0, 0, {0}, {0}};
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
        ".size xyf_hang_vector, .-xyf_hang_vector\n"
        ".align 2\n"
        ".type xyf_hang_run, STT_FUNC\n"
        "xyf_hang_run:\n"
        "    CLI R0;\n"
        "    P0.H = xyf_crash_stack;\n"
        "    P0.L = xyf_crash_stack;\n"
        "    P1 = [P0];\n"
        "    SP = P1;\n"
        "    FP = SP;\n"
        "    SP += -12;\n"
        "    CALL xyf_hang_show;\n"
        "4:\n"
        "    IDLE;\n"
        "    JUMP 4b;\n"
        ".size xyf_hang_run, .-xyf_hang_run\n");
extern "C" void xyf_hang_vector() __asm__("xyf_hang_vector");
extern "C" void xyf_hang_run() __asm__("xyf_hang_run");
void hang_disarm() {
    if (!hang_armed) return;
    uint32_t m;
    __asm__ volatile("CLI %0;" : "=d"(m) : : "memory");
    m &= ~ivg6;
    mmr(tcntl) = 0;
    *reinterpret_cast<volatile uint32_t*>(evt6) = hang_saved;
    __asm__ volatile("CSYNC;" : : : "memory");
    hang_armed = 0;
    __asm__ volatile("STI %0;" : : "d"(m) : "memory");
}
void hang_arm() {
    if (hang_armed) return;
    hang_count = hang_shown = 0;
    hang_seen = hang_beat;
    hang_last = cycles_div1024();
    hang_saved = word(evt6);
    *reinterpret_cast<volatile uint32_t*>(evt6) = reinterpret_cast<uint32_t>(&xyf_hang_vector);
    mmr(tcntl) = tmpwr;
    mmr(tscale) = 0;
    mmr(tperiod) = tick_cycles;
    mmr(tcount) = tick_cycles;
    __asm__ volatile("CSYNC;" : : : "memory");
    mmr(tcntl) = tmpwr | tmren | tautorld;
    hang_armed = 1;
    uint32_t m;
    __asm__ volatile("CLI %0;" : "=d"(m) : : "memory");
    __asm__ volatile("STI %0;" : : "d"(m | ivg6) : "memory");
}
// A readable byte for the hang screen's message (crash::copy_text): the stock image and data, L1 data.
bool hang_byte(uint32_t a, int& c) {
    if (!((a >= 0x88000000u && a < 0x88b00000u) || (a >= 0x11900000u && a < 0x11910000u) ||
          (a >= 0x11b00000u && a < 0x11b02000u)))
        return false;
    c = *reinterpret_cast<const volatile uint8_t*>(a);
    return true;
}
bool hang_word(uint32_t a, uint32_t& v) {
    if ((a & 3u) || a < 0x80000000u || a >= 0x90000000u) return false;    // SDRAM (B's stack)
    v = word(a);
    return true;
}
extern "C" __attribute__((used, noinline)) void xyf_hang_tick(uint32_t reti, uint32_t fp) __asm__("xyf_hang_tick");
extern "C" __attribute__((used, noinline)) void xyf_hang_tick(uint32_t reti, uint32_t fp) {
    // The sticky TINT: cleared by writing 0 (BF5xx) or 1 (W1C); whichever leaves it clear.
    mmr(tcntl) = tmpwr | tmren | tautorld;
    if (mmr(tcntl) & tint) mmr(tcntl) = tmpwr | tmren | tautorld | tint;
    const uint32_t now = cycles_div1024();
    if (ms_since(hang_last) < 250u) {              // a storm (TINT not cleared): stop looking, never stall B
        hang_last = now;
        mmr(tcntl) = 0;
        return;
    }
    hang_last = now;
    const uint32_t active = word(ipend);
    if (hang_held || hang_beat != hang_seen || !(active & levels_7_14)) {
        hang_seen = hang_beat;
        hang_count = 0;
        return;
    }
    if (++hang_count < xyf::crash::hang_ticks || hang_shown) return;
    xyf::crash::Hang& h = hang_record;
    h.ipend = active;
    h.pc = reti;
    h.fp = fp;
    h.caller = 0;
    hang_word(fp + 4, h.caller);
    uint32_t text = 0;
    h.text[0] = 0;
    if (hang_word(fp - 4, text) && !xyf::crash::copy_text(h.text, sizeof(h.text), text, hang_byte)) h.text[0] = 0;
    xy_forever::usb_service::crumb(h.crumb, sizeof(h.crumb));
    xyf::crash::hang_describe(h, where_word_now, hang_text);
    hang_shown = 1;
    xyf_hang_run();                                 // never returns
}
void hang_show() __asm__("xyf_hang_show");
__attribute__((used, noinline)) void hang_show() {
    for (uint32_t i = 0; i != 10; ++i) {
        const uint32_t sent = result.presents;
        present();
        if (result.presents != sent) return;
    }
}

void crash_on() {
    where_screen = 1;                               // key_ui_where.ipp: a long wait may paint its code from here
    hang_arm();
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
const xyf::crash::Hang hang_record = {0, 0, 0, 0, {0}, {0}};
const xyf::crash::HangText hang_text = {{0}, {0}, {0}, {0}};
void crash_on() {}
void crash_off() {}
#endif
