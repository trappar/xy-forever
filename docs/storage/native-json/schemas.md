# Native JSON record schemas

The native adapters (`native/json.hpp`, `xy_forever/native_json.py`,
`clients/ts/src/native-json.ts`) define these independently versioned schemas.
Storage migration is being implemented in issue #95. Host package admission,
modkit rebuilds, package indexes, client metadata reads, native library/cold boot,
content switches, journal source validation, package reports, the Mods page and
mod assembly accept the native package record. Configuration and default writers
still use legacy records. This is a partial migration.

Every native record has a string `schema` and integer `schema_version` (currently
1). The package's `version` remains display/equality metadata; it is unrelated to
the schema version. An unsupported schema or schema version refuses the record.
Stock content packs can also have a `manifest.json`: their metadata is a different
schema and is never interpreted as a native package just because of its filename.

## Package (`manifest.json`)

Identity: `"schema":"xy-forever.package","schema_version":1`.

The [package fields and dependencies](../packages.md) retain their semantic limits.
`type` is required in JSON. Names, versions, hashes, path tokens and source/licence
metadata remain strings. `bytes` and `machines` are integers. Mod package extras
from [mod packages](../mods.md) are typed as follows:

| JSON type | Fields |
|---|---|
| unsigned 32-bit integer | `module_base`, `module_bytes`, `stock_bytes`, `edits`, `objects`, `extensions`, `code`, `data`, `state`, `runtime_code`, `runtime_data`, `runtime_state`, `catalog_engine_at`, `catalog_fx_at`, `catalog_seq_at`, `stock_records`, `reserved_bytes`, `heap_fill_base`, `heap_fill_bytes`, `expanded_module_base`, `order`, `link`, `presets` |
| unsigned integer or `null` | `engine`, `effect`, `player`, `page` (legacy `-` becomes `null`) |
| array of strings | `services`, `features`, `claims`, `accessors` (legacy `-` becomes `[]`) |
| pair of unsigned integers or `null` | `catalog_number` (legacy `start,end` or `-`) |
| number | `cpu_peak_percent` (display metadata) |

Other extras remain bounded strings. Address fields are JSON integers; the
internal text view renders addresses as hex because existing linker/patch readers
use hex. No internal view is saved as a second competing record. Native semantic
readers still own type-specific required fields, dependency and integrity checks.

## Configuration (`config.json`)

Identity: `"schema":"xy-forever.config","schema_version":1`.

`desired` is an object with `base`, `profile`, `generation`, `memory`
(`standard`/`expanded`), boolean `unverified`, and a `mods` array of package folder
names (including `ext-` for extensions). The list has no duplicates. A missing
package remains represented in the saved selection rather than disappearing.

`built` is `null` before the first build, or an object with `base`, `generation`,
unsigned integer `set` (the existing order-independent CRC sum), integer `count`,
`memory`, `slot` (`mods-a`/`mods-b`), and `sha256` (the built firmware stamp).
This records generated build bookkeeping separately from the desired selection.
The existing build-reuse rule remains base/generation/set/count/memory plus a
matching slot stamp; JSON does not change that rule.

## Bounds and parser

The pinned MIT tiny-json revision is
`025cdde5f2574290d8b2a8524bc257db2829bb7c`; provenance and the minimal
freestanding include adjustment are in `third_party/tiny-json/README.md`.
Its caller-owned property pool and mutable input copy require no heap allocation
or recursion. The raw source bytes remain intact for integrity checks and journal
source identity. There is no extra strict JSON syntax validator.

| Bound | Limit |
|---|---|
| serialized document | 8448 bytes |
| properties (root/containers/values included) | 128 |
| package payload fields | 32 (schema identity excluded) |
| object children / array items | 34 / 96 |
| container nesting | 4 (root counts as the first) |
| field name / text value | 31 / 127 printable ASCII bytes |
| internal package / configuration view | 4096 / 2048 bytes |
| configuration identifier | 31 bytes |

The 8448-byte input bound allows JSON escaping overhead while preserving the
legacy semantic capacities: a 4096-byte text fixture with quoted values becomes
8202 JSON bytes. Field-specific capacities and type/range checks still apply.
All duplicate object keys are refused, including unknown keys. Scalar numbers
used by native operations must be unsigned integers through `2^32-1`; booleans
and numeric strings are not integers. These fields use unsigned decimal integer
representations: no minus sign, fraction or exponent. Browser checks retain that
representation before JavaScript can round a fractional number into an integer.

Native text fields retain printable ASCII. Writers emit ASCII directly and escape
quotation marks/backslashes. Unicode escapes (`\uXXXX`) are explicitly refused:
the pinned parser would replace them with `?`. A literal backslash followed by
`u`, encoded as `\\u`, remains intact. Decoded control characters and non-ASCII
text are refused. Shared fixtures live in `tests/native-json.json`.

On the Blackfin, the caller-owned workspace is 11,012 bytes: 8,452 bytes for the
input/NUL/alignment and 2,560 bytes for 128 properties. This does not include the
caller's internal text output or surrounding job workspace. Stack and linked
firmware growth must also be measured when the adapters enter live readers.

Live caller ownership and integrated resource measurements: [native readers](readers.md).

## Host package record selection

Python `package_records` and TypeScript `package-records` select `manifest.json`
when present and use `info.txt` only when JSON is absent. A malformed native
record, wrong schema or unsupported schema version refuses; a second legacy file
does not authorize fallback. Metadata-only library/display reads retain the old
lenient interpretation of legacy text. Native JSON always passes through its
bounded schema adapter.

Strict package admission requires the selected manifest's original bytes to be
covered by `files.sha256`. Hash checks use the original document, before creating
any internal text view. A directory containing both records whose list covers
only `info.txt` is refused. Package archives retain their exact bytes, including
metadata; index fetch does not normalize or migrate them. Shared host integration
cases are in `tests/package-records.json`; adapter cases shared with C++ remain in
`tests/native-json.json`. Stock content-pack readers keep their own schema and
text/size rules; the native adapter is used only in native package contexts.
