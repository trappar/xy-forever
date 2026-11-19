# Read-only boot-partition diagnostic (#7)

Part of [Hardware and stock-code facts](../HARDWARE.md); see [stock storage requests](storage-server.md).

`scripts/boot_probe.py` uses developer command `0x64`, version 1: begin, read either 512-byte header,
read one image sector, end. A samples only DDR word `88a1f3c4` and byte `88a291c0`. Its dedicated gate
admits partition selects 0..2 and single-sector reads; it rejects trim, writes and file operations.
The probe uses the already-admitted builders/executors and B's bounded file-client polling, not the
stock blocking client or the install exemption. Ordinary menu/file/MTP operations wait while it owns
the storage slot. The write window closes before begin; a failed select remains a restoration
obligation even if it already changed the hardware partition. Only confirmed select 0 permits end.

The host saves both headers, the complete active image and `xyf-boot-probe/1` JSON under the private
sessions folder. Image hashes describe raw stored bytes (possibly encrypted), not decrypted firmware.
Transport/link loss stops commands and leaves restoration unknown; no reset is attempted. After a
responsive failure, `scripts/boot_probe.py --finish` can retry select 0 and end. A normal run finishes at
the menu under keepalive. The probe writes no storage and reads no key; its #8 recommendation is not
permission to run a new boot writer.

The 1.2.1 device check with build `7e914ede` found both A values zero and completed both header
reads, the active-image read and restoration to partition 0. These preconditions support #8's
A-side writer design; no A-side writer was run.
