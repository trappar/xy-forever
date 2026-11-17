# Hardware and stock-code facts

Reference for the OP-XY and its stock 1.2.1 bootloader, as used by XY Forever's
cold code. Addresses are processor addresses in the 1.2.1 bootloader image unless
noted. The full listing lives in the private folder (`LISTING_121` in
`xy_forever/paths.py`); evidence is cited by decision record. Verify claims against the
raw listing: Ghidra's decompiler C is unreliable for parallel instructions and indirect jumps.

## Contents

- [Boot and memory](hardware/boot.md): versions and identities, boot and processors, the memory map (product, build 171)
- [Keys, display, storage](hardware/devices.md): keys (B, through the PSoC), the display (B), storage (A, yaffs)
- [Boot partition and USB](hardware/usb.md): the boot partition and USB (B), the stock USB function-driver API (unverified)
- [Stock storage server requests](hardware/storage-server.md): role 0 requests, as MTP uses them
- [Firmware packages](hardware/tfw.md): the `.tfw` format, the OTP boot-ROM table
- [Reverse-engineering tools](hardware/tools.md): the listing scripts in `re/`, Ghidra
