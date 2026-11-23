# Hardware and stock-code facts

Reference for the OP-XY and its stock 1.2.1 bootloader, as used by XY Forever's
cold code. Addresses are processor addresses in the 1.2.1 bootloader image unless
noted. The full listing lives in the private folder (`LISTING_121` in
`xy_forever/paths.py`); research evidence and decisions live in GitHub issues. Verify claims against the
raw listing: Ghidra's decompiler C is unreliable for parallel instructions and indirect jumps.

## Contents

- [Boot and memory](hardware/boot.md): versions and identities, boot and processors, the memory map (product)
- [Keys, display, storage](hardware/devices.md): keys (B, through the PSoC), the display (B), storage (A, yaffs)
- [Boot partition and USB](hardware/usb.md): the boot partition and USB (B), the stock USB function-driver API and ownership rules
- [USB identity](hardware/usb-identity.md): ids, names and the serial in each mode, who sets them, what a browser remembers a permission by
- [Stock storage server requests](hardware/storage-server.md): role 0 requests, as MTP uses them; protected inbox paths
- [Stock contracts](hardware/stock-contracts.md): restart order, B's fetch and request calls, WDOG0, the dev-menu marker, upgrade resume
- [Write bodies and concurrency](hardware/write-concurrency.md): shared buffers and stock write serialization
- [A install staging rehearsal](hardware/a-install-staging.md): RAM transfer, ownership and no-write admission
- [Read-only boot-partition probe](hardware/boot-partition-probe.md): protocol, restoration and measured A preconditions
- [Firmware packages](hardware/tfw.md): the `.tfw` format, keys, signatures, package contents, companion hazards
- [OTP and the package check](hardware/otp.md): the OTP boot-ROM table, XY Forever's reader, `tfw.check`, the pack import's reader
- [Reverse-engineering tools](hardware/tools.md): the listing scripts in `re/`, Ghidra
