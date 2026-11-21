# tiny-json

Upstream: https://github.com/rafagafe/tiny-json

Pinned revision: `025cdde5f2574290d8b2a8524bc257db2829bb7c` (MIT).
`LICENSE` is unmodified. Local changes to `tiny-json.c`/`tiny-json.h` are
`XYF_TINY_JSON_FREESTANDING` (omit libc includes and unused numeric getters)
and a privacy-scanner annotation on the retained public copyright attribution.
Parser behavior and implementation are unchanged.
`native/json.cpp` supplies freestanding character/string helpers and uses only the
caller-owned property pool; optional libc numeric getters are unused.

The native adapter rejects Unicode escapes before parsing because this revision
replaces every `\uXXXX` with `?`. Native writers emit printable ASCII directly
(and escape only quotation marks and backslashes). This preserves existing native
field character limits without silently changing identifiers or hashes.
