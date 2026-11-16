// A separate, bounded 16 KiB stack; the retained 4 KiB stack is insufficient
// for native storage calls. No caller return is attempted after the ROM load.
.section .text.entry,"ax",@progbits
.align 2
.global xy_forever_cold_entry
.type xy_forever_cold_entry, STT_FUNC
.extern xy_forever_cold_run
xy_forever_cold_entry:
    P0.H = 0x8ab1;
    P0.L = 0;
    SP = P0;
    FP = SP;
    [--SP] = RETS;
    SP += -12;
    CALL xy_forever_cold_run;
1:
    IDLE;
    JUMP 1b;
.size xy_forever_cold_entry, .-xy_forever_cold_entry
