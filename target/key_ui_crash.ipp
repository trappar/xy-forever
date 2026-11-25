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
const uint32_t crash_shown = 0;
const xyf::crash::Text crash_text = {{0}, {0}, {0}, {0}};
void crash_on() {}
void crash_off() {}
#endif
