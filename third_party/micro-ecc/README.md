# micro-ecc

Upstream: https://github.com/kmackay/micro-ecc

Pinned revision: `76fda53af41aed0a5b372227be65f69fcb502400` (master, 2026-11-17; after the
`v1.2` tag, with its later security fixes). Licence: BSD 2-clause, `LICENSE.txt` unmodified.

Only the files `uECC.c` includes for our configuration are kept (the ARM and AVR assembly, the
tests and the build scripts are not). `native/p224.cpp` includes them, configures P-224 with 32-bit
words, and uses it for ECDSA verification only (`p224::verify`, the `.tfw` header check).

## Local changes

Both keep the freestanding audit (`tests/test_xyf_freestanding.py`) unchanged:

- `uECC.h`, `uECC_vli.h`: the `extern "C"` blocks are skipped when `XYF_UECC_CXX_LINKAGE` is
  defined, so the functions get C++ linkage and the audit can tell them from outside calls.
- `curve-specific.inc`, `x_side_default`: `uECC_word_t _3[uECC_MAX_WORDS] = {3};` became a
  `uECC_vli_clear` and a store, because GCC compiles that initializer into a `memset` call.

`native/p224.cpp` also uses two of micro-ecc's own porting hooks, without changing its files:
`asm_mult` with a multiply built from 16 x 16-bit products (its generic one needs a 64-bit
multiply, a libgcc `__muldi3` call on the Blackfin), and `default_RNG` when freestanding (one that
always fails: verification draws no random numbers; it keeps the RNG pointer out of BSS).

## SHA-256

Upstream files, as fetched:

- `LICENSE.txt`: SHA-256 `ffd8b033d2df7568c25a98866bd92b1656f7da82d7f813c0b2eb85ec36611193`
- `uECC.c`: SHA-256 `34f96be9728ce43d72dd8c110c58d1c993fa21f48f9ec0cc1990e2f3e33e685e`
- `uECC.h`: SHA-256 `9d480aa6920155f68503064bebefb815a82aab318ab305efdcf04674d2a9cba7`
- `uECC_vli.h`: SHA-256 `a8ff7957855e6520674cad4bcaf8f3e752d40f6c3f7501915c3504c777be1e7a`
- `types.h`: SHA-256 `61bd255c0dca4c69dfc779cc5c1fe1cbff47039d4bb7f37578a88f63b7fca466`
- `curve-specific.inc`: SHA-256 `9e9d8d6771731dce2e084a4469d0dd2e9ac6245c8773766198a83f82d24b6313`
- `platform-specific.inc`: SHA-256 `e0f036628b47fb7180bfaeb0903a41edbbcda8e1ef99098456eb3b1cdcb4531e`

The three changed files here:

- `uECC.h`: SHA-256 `376264eefe42296e6a8aa295c90697242475a3fbff5576360076bfb1907ed949`
- `uECC_vli.h`: SHA-256 `8b0f8a2fe500f08b8edfed4a68a4d20d726a241e35bac35afdf93910a2b17d80`
- `curve-specific.inc`: SHA-256 `0d3144dec0b1ba90b608a143910f7cff2bb6c1161948a4c53d598362313776dc`
