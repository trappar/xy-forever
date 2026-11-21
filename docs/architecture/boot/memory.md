# Memory map (product)

Part of [boot architecture](../boot.md).

Table and owners: [hardware/boot.md](../../hardware/boot.md#memory-map-product). The module (code window `0x8ab00000..0x8ab80000`), its stack
(`..0x8ab84000`) and the dormant stage-2 window (`0x8ab90000..0x8ac00000`) lie in `0x8aa65bf4..0x8b000000`, above the stock allocator arena and below B's mod
job region; `xyf_build.py` `check_memory_map` (build and `--verify`) fails the build if either layout leaves that gap or touches the boot source, job region,
staging, framebuffer or report slots.

The offline stage-2 demos use `0x8ab00000..0x8ab0e000` for the cold module and
`0x8ab0e000..0x8ab12000` for its 16 KiB stack. They share the product's memory map audit.

- **Free at power-on:** outside every static bootloader segment and the arena (both build-checked); the 1.2.1 bootloader's disassembly loads no immediate in
  `0x8a000000..0x8c000000` but its stream buffer `0x8a000000`, and its initialized data holds no aligned pointer into `0x8ab00000..0x8ac00000` (one byte-table
  word at `0x1190029c` looks like one); its load path reads at most 8 MiB into `0x8a000000` (`88a34a12`) and then places our two records. Our own buffers are
  all elsewhere; peer DMA (`owned_buffer_range`) refuses `0x8ab00000..XYF_STACK_END`. The bounds are enforced offline;
  byte checks alone do not qualify a new cold-time memory use on the device.
- **Hand-off:** the image is copied from staging to `0x8a000000` (at most `staging_bytes` 0x7e0000 B, so below `0x8a800000`); every record is admitted by
  `cold.cpp`'s policy, whose protected range `0x8ab00000..XYF_STACK_END` covers the running code and stack, before the stock RAM loader (also protected) places
  it. Stock 1.2.1 (2,373,336 B), 1.2.5's `os.ldr` and a device-assembled mod image (2,493,876 B) place nothing in the gap
  (`test_official_images_pass_the_product_policy`); any image up to the 8 MiB boot source meets the same check, so size does not matter.
