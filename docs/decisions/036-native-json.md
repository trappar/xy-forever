# 036: Native records use JSON with tiny-json

Decision, 2026-11-17 (issue #96). Native-file implementation and migration are #95.

Use JSON for XY Forever's native metadata and configuration, with **tiny-json** at
`025cdde5f2574290d8b2a8524bc257db2829bb7c`. Use it until a required behavior or a
measured resource limit proves it insufficient. Do not write a general-purpose JSON
parser or add a strict validator merely to reject harmless syntax deviations.

External Open-XY layouts remain browser onboarding input ([035](035-layout-migration.md));
the device understands only native records. This decision changes no runtime code.

## Why JSON

The browser and device generate these records; hand editing is secondary. JSON has
native browser support and represents the lists and structured fields we need.
YAML/TOML features aimed at hand editing add little to this workflow; no claim is made
about their target parser sizes. Use an established small parser for our actual needs.

## Dependency and measured cost

[tiny-json](https://github.com/rafagafe/tiny-json/tree/025cdde5f2574290d8b2a8524bc257db2829bb7c)
is MIT licensed. Preserve its notice and pin when vendoring.

The authenticated BF707 toolchain compiled the unchanged library with repository
`CXX_FLAGS`, replacing `-O2` with `-Os` and adding `-fstack-usage`, as the build does.
The full library object contained **1,280 B of code and 96 B of read-only data**:
**1,376 B total**, with zero data/BSS and no libgcc imports. Undefined helpers were
`isdigit`, `isxdigit` and `strcmp`.

These are library-object measurements, not net firmware growth. Linked code,
freestanding helpers, field handling and working memory must be measured during
integration. Optional numeric getters can introduce additional runtime dependencies;
include only what our fields need. The upstream tests passed six groups/100 checks.

## Integration

The parser builds a typed property tree in a fixed caller pool, without heap
allocation or recursion. It modifies a NUL-terminated input buffer. Bound that buffer
and the pool; include both in the RAM budget. An assembly-derived estimate put its
create call chain at 124 B, excluding callers and external libc functions. This is not
a runtime watermark; compiler `.su` output undercounts Blackfin frame costs.

Check the top-level type, required fields, versions, field types, lengths and numeric
ranges needed by native operations. Keep used fields unambiguous and refuse newer
unsupported versions with a message. These are application/schema checks, not a
second JSON parser. Permissive handling of some commas or trailing input is not by
itself a reason to replace the dependency.

The pinned [Unicode escape decoder](https://github.com/rafagafe/tiny-json/blob/025cdde5f2574290d8b2a8524bc257db2829bb7c/tiny-json.c#L114)
replaces every `\uXXXX` escape with `?`, including `\u0041` (which represents `A`).
Verify round-tripping of the strings our browser, Python and device writers actually
produce; do not silently accept changed values. If a required string behavior cannot
be supported simply, or measured code/RAM costs are unacceptable, revisit the parser
with that concrete evidence. Do not preemptively build a Unicode decoder or strict
syntax layer around it.

## Repeating the measurement and cleanup

Fetch the exact upstream pin into a temporary directory and run `make -C test test`.
For target sizes, supply minimal freestanding headers/helpers, use the flags above,
assemble with `-mcpu=bf707`, and inspect sections/imports/disassembly with
`bfin-elf-objdump -h`, `-t` and `-dr`. Keep all entry points for the object comparison;
measure the actual linked native adapter separately.

Temporary checkouts, harnesses, outputs and archives are removed after the evaluation.
No private experiment scripts are retained; public pins, methods and findings above
are the durable record. Integration must preserve this cleanup practice.
