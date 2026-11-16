// Exact stock synth-initializer epilogue. The hook replaces SP+=24 and the
// register-pop pair; the stock frame remains live across our C++ call.
.section .text.entry,"ax",@progbits
.align 2
.global xy_forever_report_entry
.type xy_forever_report_entry, STT_FUNC
.extern xy_forever_storage_report
xy_forever_report_entry:
    SP += -12;
    CALL xy_forever_storage_report;
    SP += 12;
    R0 = 0;
    SP += 24;
    (R7:7, P5:3) = [SP++];
    UNLINK;
    RTS;
.size xy_forever_report_entry, .-xy_forever_report_entry
