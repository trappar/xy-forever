# tiny-AES-c

Upstream: https://github.com/kokke/tiny-AES-c

Pinned revision: `23856752fbd139da0b8ca6e471a13d5bcc99a08d` (master, 2024-10-04; after the `v1.0.0`
tag). Licence: the Unlicense (public domain), `unlicense.txt` unmodified.

`native/aes.cpp` includes `aes.c` for AES-256 with ECB only, and uses its key expansion and block
decryption (what `AES_init_ctx` and `AES_ECB_decrypt` do) on `crypto::Aes256`'s own key schedule,
which it erases. The `.tfw` reader keeps its own random-access CBC over single blocks.

## Local change

- `aes.c`: `#include <string.h>` is skipped when `XYF_TINY_AES_FREESTANDING` is defined. The firmware
  toolchain has no libc headers, and ECB uses no string functions.

## SHA-256

Upstream files, as fetched:

- `aes.c`: SHA-256 `f7f78b44654efd3542b6886923dd05194286807ebcfeb078ea05d21fe901ee9e`
- `aes.h`: SHA-256 `9f74a4de3bd11621ff6e8fb9b352b060f699e6c1c67f81a22d104aacad61a23b`
- `unlicense.txt`: SHA-256 `7e12e5df4bae12cb21581ba157ced20e1986a0508dd10d0e8a4ab9a4cf94e85c`

The changed file here:

- `aes.c`: SHA-256 `d004a1db4c16c69e1554842f13a10267247fa81e23f99d5e3db2831518d2bddf`

## Test vectors in `aes.c`

Its header comment quotes the public NIST SP 800-38A F.1.1 ECB-AES128 test vectors (none is a key
of ours); listed here so the leak check knows them:

- test vector `6bc1bee22e409f96e93d7e117393172a`
- test vector `ae2d8a571e03ac9c9eb76fac45af8e51`
- test vector `30c81c46a35ce411e5fbc1191a0a52ef`
- test vector `f69f2445df4f9b17ad2b417be66c3710`
- test vector `2b7e151628aed2a6abf7158809cf4f3c`
- test vector `3ad77bb40d7a3660a89ecaf32466ef97`
- test vector `f5d3d58503b9699de785895a96fdbaaf`
- test vector `43b1cd7f598ece23881b00e3ed030688`
- test vector `7b0c785e27e8ad3f8223207104725dd4`
