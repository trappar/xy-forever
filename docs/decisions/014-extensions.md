# 014: Open-XY extensions

Research record, 2026-11-14. Sources: `../open-xy` checkout `4718876` (branch `sdk/synth-postmix`, 0.0.4: capture only, 166 edits); `origin/main` `52c8de9`
(0.0.6, the SDK of the published kit; 205 edits); `origin/staging` `fd66c4a` (0.0.6+, 217 edits); the kit 0.0.6 of 008 (the private fixture
`paths.PUBLISHED_KIT`). Facts are from main/kit unless named. Extension source repositories were not read. No device, no firmware build, no network. **[C]**
read from code/docs, **[M]** measured (kit objects parsed; main's `builder/core.mjs` `linkKit` run under Node on the kit, no stock OS), **[I]** inferred,
**[U]** unverified.

**Question:** what extensions are, how open-xy attaches them, and how each becomes an on/off package in 008's base-plus-modules assembly, with nothing loaded
when off.

## Answer in brief
An extension is one compiled object plus a few metadata fields. It owns **no firmware edits**. Its hooks are SDK "services" in the profile, as for machines: one
service (`extensions`, 7 hooks) feeds every event extension, and one (`master_filter`, 8 hooks) feeds every master processor. Each service is applied only when
a selected extension needs it [C `docs/extensions.md`, `core.mjs linkKit`]. So an extension fits 008 as one package that names the services it uses. The base
already holds every edit as data. Off: the object is not linked, the service stays unapplied (stock bytes) when no other extension needs it, and the accessor
symbols go to `kit_absent`. **Extensions can be in the first version**: the same mechanism, about 250 device lines and 120 host lines more, checked byte for
byte with `core.mjs`, which takes the extension list as an argument.

## The extensions
| id | Status | What the player gets | Uses |
|---|---|---|---|
| `capture` (Retrospective capture) | Core, published, `open-xy-core-machines` v1.6.1 `efa880a` | While playing and not recording, the firmware keeps what you play live on the selected track. Double-tap Record to put it into the pattern at its loop positions; a "captured" popup confirms. Up to 120 notes [C README] | claim `record_double_tap`, actions `edit_notes`, `notify` |
| `master_dj_filter` (Master DJ Filter) | community (V714), published, v1.1.5 `79f6375` | Replaces the master EQ with a resonant one-knob low-pass/neutral/high-pass filter with an animated screen. You choose it in the stock filter selector (Shift+M2 on MIX page 2). Its setting is saved per project in `.oxy` [C `docs/master-filter.md`] | `master_processor` (state `dj`, artwork `master_dj_filter.svg`, 16,117 B) |
| `checkpoint` | draft, main + staging catalog only, not in the kit | unknown [U] | ? |
| `drumline_page` | staging only (mmockett v1.0.0) | A second page for stock Drumline: 1 to 4 settings per key, step locks, saved in each pad's record [C staging `docs/drumline-page.md`] | exclusive like a claim; services `drumline` + drum/sample services |

## How they attach (evidence)
- **Made of** [M]: one object per extension, `sdk/extension_<id>.o`, tagged `extension:<id>` in the kit. capture: 4,736 B file, 8,100 B in memory (5,864 B of it
  BSS), no imports. DJ filter: 41,396 B file, 36,208 B in memory; it imports 3 SDK master-processor symbols, the arithmetic helpers and `_memset`. Neither names
  a stock address, a machine or another extension. Required export: `sdk_extension_<id>_hooks`, plus `_processor`/`_panel` (or `_filter`) for a processor
  [C `openKit`].
- **SDK service objects** [M]: `extensions` = `extension_runtime.o`, `extension_collection.o`, `extension_stock.o` (5,220 B). `master_filter` =
  `master_filter_stock/hooks/panel.o` (4,936 B). In every build, extensions or not: `master_filter_state.o` + `master_filter_collection.o` (1,992 B, the
  companion support that reads saved processor state).
- **Selection → edits** [M, Node linkKit, fm2op alone]: none 73 edits / 148,120 B allocated. capture 80 / 161,440 (+13,320). DJ filter 88 / 194,484 (+46,364).
  Both 88 / 202,584 (+54,464). The 7 edits = the `extensions` hooks (controller update, key queue, live note on/off, recorder tick, stop, note accepted). The 8
  = the `master_filter` hooks (MIX buttons, shifted buttons, encoder, EQ frame, parameter receiver, audio call, project publication, new project). Any extension
  turns on all 7, even one that takes no events. A hook's bytes depend on where `sdk_extensions_*` lands, so they depend on the selection, as 008 found for
  machine services.
- **Selection rules** [C `extensionSelection`, `linkKit`]: at most 16 extensions. The order is collection order, never tick order; it is the callback order. Two
  extensions with the same claim are refused (staging: the same for `drumline_page`). Master processors need unique `state_id` and asset names, and several may
  be included (the chooser lists them). `linkKit` needs at least one machine. Unselected extensions' accessors alias `kit_absent`. Extensions take no machine
  numbers.
- **Generation-bound** [M]: `extension_collection.o` and `master_filter_collection.o` name every kit extension. The interface is source-level, not a binary ABI
  [C]. So an extension package works only with the base from the same export, as machines do.
- **Not in the kit** [C `tooling/browser_kit.py`]: extension entries carry no source pin and no licence (notices cover only the SDK, the runtime and machines'
  `vendor/LICENSE`).

Detail: Design, Mod-assembly changes, What 008 needs in [014-extensions.detail.md](014-extensions.detail.md)

## Safety
At assembly, as 008 plus: every profile edit's preimage and mapping match stock, active or not (`patchFirmware`). Only the base's edits (profile SHA-256 pinned)
are applied; a package carrying edits or addresses is refused. Our packager also refuses an extension object that imports anything but SDK or runtime exports
(stricter than `core.mjs`, bytes unchanged). Every hook lands inside the module, no intervals overlap, and undoing the edits and removing the record gives
stock's SHA-256. The image keeps 008's shape (one loader record at `0x8d2d0380`) and must pass the library's cold LDR policy (`target/cold.cpp`: allowed ranges,
protected ranges) like any entry. **User experience:** the new entry is offered as boot once. If it hangs, a power cycle boots the old default, and PLAY at
power-on opens the XY Forever menu (turn the extension off, Apply). An extension's code runs only on use (capture on a double-tap while playing; DJ filter once
chosen), so boot once proves less than it does for a synth: try the feature in that session before making the entry the default.

## Licences
SDK, runtime glue and service objects: MIT [C]. capture: open-xy-core-machines, licence not in the kit [U]. DJ filter: V714's repository, licence [U]. The
catalog requires redistributable licences [C `docs/catalog.md`]. Personal use is unaffected. Before publishing packages, read each pinned repository's licence.
