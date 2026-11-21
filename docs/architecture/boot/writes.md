# Boot writes

Part of [boot architecture](../boot.md).

Boot state uses power-safe A/B slots (`state-a.txt` / `state-b.txt`).
Sequence on A: read-only unmount → stock callbacks → read-write mount → write ≤ 4 KiB → read back →
read-write unmount → gates back → read-only remount. Writes are exact-path checked and happen only on an
explicit user/host action or a power-on convergence; a power-on whose fingerprint matches writes nothing. The boot partition is written only by the stock
writer `88a06a7e` (commit-last A/B, power-loss safe).

### A admission rehearsal (#8, phase 1)

Product developer command `dev.a_rehearsal` transfers the current verified USB staging input from
B's `0x8b820000` into **A's separate RAM** at the same address. It never invokes a writer. The
cooperative B pump leaves one bounded file-client request out per pass; USB, menu and status keep
running while A is delayed. Shipping B install/uninstall keep their existing path.

A owns its existing file-boot staging exclusively during begin/chunk/finalize/proof/abort. Begin
revokes normal CHECK preparation before mutation; competing commands/requests revoke the lease
and are refused before scratch, mount or file work. A checks version, token replay, exact contiguous
offsets/size, chunk CRC, its own complete CRC/SHA and the existing install or stock-uninstall
admission policy. The extra padding sector remains within the original reservation. An admitted
install buffer never becomes a BOOT/default prepared image. Success collects A's digest and then
aborts the lease; failure, cancellation, timeout and session end also release logical ownership.
Transport failure keeps the existing lost-link behavior, with no reset, retry or fallback.
