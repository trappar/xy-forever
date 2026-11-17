# Hardware and stock-code facts: reverse-engineering tools

Part of [Hardware and stock-code facts](../HARDWARE.md).

## Reverse-engineering tools

- **Listing tools:** `re/bootloader.py ADDR [-d N] [-a]` summarises functions;
  `re/reach.py ROOTS` lists reachable functions; `re/spans.py REACH...` generates CRC
  spans; `re/mtp_reach.py` runs the MTP reach and forbidden check. They read the
  private listing and image, plus census reports from
  `REPORTS` (`xy_forever/paths.py`).
- **Ghidra:** `../tools/ghidra_12.1.4_PUBLIC` with the Blackfin module
  `../tools/Ghidra-BlackFinPlus`, user dir `../tools/ghidra-user`, JDK
  `../tools/jdk-21.0.13+9`.
  - Projects: one for OS 1.2.1 and one for the 1.2.1 bootloader, kept under
    `ANALYSIS` (`xy_forever/paths.py`).
  - Never run two writers on one project. REA has no Blackfin provider.
