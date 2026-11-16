// XY Forever stage 2: the ONE deliberate register call in stage 1.
//   uint32_t xyf_stage2_enter(uint32_t entry, const xyf::stage2::Abi* abi)
// Tail-jumps to the validated stage-2 entry (R0) with the ABI pointer as its
// first argument; stage 2's RTS returns straight to our caller with its exit
// code in R0. The builder's call audit accepts a register jump only inside
// this symbol (--xyf-stage2), and requires exactly one there.
.text
.align 2
.global xyf_stage2_enter
.type xyf_stage2_enter, STT_FUNC
xyf_stage2_enter:
    P0 = R0;
    R0 = R1;
    JUMP (P0);
.size xyf_stage2_enter, .-xyf_stage2_enter
