#ifndef XYF_COLD_LAYOUT_HPP
#define XYF_COLD_LAYOUT_HPP
// Cold module + stack reservation (xyf_build.py layout()). Every mode
// uses 0x8ab00000..0x8ab0c000 code + 16 KiB stack to 0x8ab10000, except the
// wide product layout (--xyf-product; 4.7x), which passes
// -DXYF_STACK_END=0x8ab84000u: a 512 KiB code window to 0x8ab80000, stack to
// 0x8ab84000, below the stage-2 window (0x8ab90000). The whole range lies above
// the stock allocator arena and below A's job region, where no stock reference,
// stock allocation or outer LDR load exists (xyf_build.py check_memory_map;
// docs/ARCHITECTURE.md, "Memory map").
// B's mod job region (5.4d2; decision 019's region, the job runs on B): the job's state and work
// region (native/mod_job, peak 6.3 MiB) in 0x8b000000..0x8b800000, above the free gap, below the
// session scratch and staging. Nothing else may claim it: xyf_build.py check_memory_map checks these
// numbers against its JOB_REGION and every other reservation; cold.cpp's protected ranges keep any
// loaded image out of it.
#define XYF_JOB_REGION_BEGIN 0x8b000000u
#define XYF_JOB_REGION_END 0x8b800000u
#ifndef XYF_STACK_END
#define XYF_STACK_END 0x8ab10000u
#else
// The stack watermark (file_boot, key_ui) paints from the module end, but in a
// passed-in layout no lower than 32 KiB below the stack top (the stack and 16 KiB
// under it, about what build 170 painted), so a large code window costs no time.
#define XYF_STACK_PAINT_FLOOR (XYF_STACK_END - 0x8000u)
#endif
#endif
