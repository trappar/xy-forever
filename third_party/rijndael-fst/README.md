# rijndael-alg-fst

The table-driven AES reference code by Rijmen, Bosselaers and Barreto (version 3.0, December 2000),
the code OpenSSL's, OpenBSD's and FreeBSD's AES derive from, as FreeBSD keeps it.

Upstream: https://github.com/freebsd/freebsd-src/blob/fdafd315ad0d0f28a11b9fb4476a9ab059c62b92/sys/crypto/rijndael/rijndael-alg-fst.c

Pinned revision: `fdafd315ad0d0f28a11b9fb4476a9ab059c62b92` (main, 2023-11-27, the file's latest change).
Licence: public domain, as its header comment says.

`native/aes.cpp` includes `rijndael-alg-fst.c` for its AES-256 decryption key schedule and block decryption
(`rijndaelKeySetupDec`, `rijndaelDecrypt`) on `crypto::Aes256`'s own key schedule, which it erases. It replaced
tiny-AES-c (#195): tiny-AES-c's byte-wise decryption took about 1,400 B cycles per byte, the T-table rounds a
small fraction of that.

## Local changes

- `rijndael-alg-fst.c`: its `#include` block (and FreeBSD's `KASSERT`) is skipped when
  `XYF_RIJNDAEL_FREESTANDING` is defined. The firmware toolchain has no libc headers; `native/aes.cpp`
  gives the `u8`/`u32` types and an empty `KASSERT` instead.
- `rijndael-alg-fst.c`: its `rcon` table is skipped when `XYF_RIJNDAEL_RCON` is defined; `native/aes.cpp` computes
  the same round constants. The table's `0x04000000` is a boot-ROM address to the firmware builder's audit of
  stored data words (`xy_forever/otp_audit.py`).

## SHA-256

- `rijndael-alg-fst.c` upstream, as fetched: SHA-256 `380098b29f8e36853f1f4fedfbbdf2c4bbe22a96918319f9589c5fb5eb36929f`
- `rijndael-alg-fst.c` here, with the local changes: SHA-256 `e45dddaa9f689b00fff7d3ac4b217041ad4a324f48eddae8236778a746ee57b3`
