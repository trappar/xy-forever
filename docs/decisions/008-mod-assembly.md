# 008: On-device mod selection

Research record, 2026-11-14, against open-xy `4718876` (branch `sdk/synth-postmix`, 0.0.4) and the published kit 0.0.6 (`opxy-browser-kit/1`, SDK `52c8de9`, 80
machines, 395 objects, 205 profile edits) (the private fixture `paths.PUBLISHED_KIT`). The core-machines repository is not checked out; its compiled objects are
in that kit. No device, no firmware build, no network. **[C]** read from code/docs, **[M]** measured (kit objects parsed; probes compiled with
`../tools/blackfin`), **[I]** inferred, **[U]** unverified.

**Question:** can XY Forever assemble a firmware from a base plus the chosen mods, once per selection change, so unchosen mods use no memory; what does it take,
and what is the smallest first version.

## Answer in brief
Practical, at moderate cost. 005 rated "link at boot" high because it assumed linking at every boot and a format of our own. Open-xy already ships what a device
linker needs: its kit holds normalized ELF32 relocatable objects per machine plus SDK objects, and `builder/core.mjs` is a ~400-line reference linker
(`readObject`, `numberLayout`, `linkKit`, `patchFirmware`, `buildAssets`) whose output the kit export checks against GNU ld byte for byte [C]. We port that
linker to freestanding C++, run it once when the user presses Apply, and store the result as an ordinary library entry. Every output can be proven on the host:
it must be byte-identical to `core.mjs` on the same inputs. **First version:** packages split from one open-xy kit, catalog machines only, OS 1.2.1 only, no
extensions.

## Evidence
- **Base (Q1)** [C profile, M counts]: the 205 edits are 26 fixed byte edits (2 heap, 21 relocated-table, 3 text), 4 `aux` text edits, 36 hooks plus the startup
  label that branch to SDK symbols, 10 profiling-off hooks, and 128 hooks in 9 services (samples 63, drums 17, aux 9, control 8, master_filter 8, extensions 7,
  players 7, drum_sends 5, drum_locks 3, octaves 1). A service is applied only when a selected machine's kind needs it. Hook bytes depend on where the module's
  symbols land, which depends on the selection. So a prebuilt "base os.ldr" would save only the 26 fixed edits, would lose byte parity, and needs TE's OS on the
  host. **Base = the library's official `opxy-121-stock` os.ldr (SHA-256 pinned by the profile) + the edit list as data, applied at assembly.**
- **Selection-dependent code** [M]: the per-collection SDK objects (`startup.o`, `benchmark.o`, `synth_control_table.o`, `project_companion_table.o`,
  `sample_callbacks.o`, `drum_callbacks.o`, `player_table.o`, …) are compiled for the kit's whole collection. They name each machine's callbacks (~7 per synth).
  An unselected machine's callbacks resolve to `kit_absent` (its `absent` map), and its numbers are absolute symbols set to 0 (`numberSymbols`). These rows cost
  ~36 KB for 80 machines, about 0.45 KB per unselected machine. The machine's code, data and state are not linked.
- **Module (Q2)** [M]: a machine is 4 to 6 objects (fm2op: `voice.o`, `display.o`, `screen.o`, `registration.o`). Sizes: ringmod 2.4 KB; fm2op 18 KB plus 7.5 KB
  state; karplus 16 KB plus 82 KB; macroosc 257 KB plus 162 KB; troupe 905 KB plus 92 KB. The SDK core is 103 KB plus 20 KB state; the samples service adds 16
  KB plus 130 KB. An fm2op-only module: 120,524 B initialized, 148,120 B allocated. Machines import only SDK symbols (25 to 35 per synth, 6 to 9 per effect) and
  their own `<prefix>_engine_id/_page_id/_fx_id`: no stock address, no other machine, no shared DSP library (vendored code is private to each machine). The SDK
  imports 11 stock addresses (`kit.definitions`). Arithmetic helpers (`sdk/runtime/_muldi3.o`) are linked only when referenced (`selectRuntime`).
- **Relocations** [M]: only 5 types occur, counted over the whole kit: `R_BFIN_LUIMM16` (6) 13,214, `R_BFIN_HUIMM16` (7) 13,214, `R_BFIN_PCREL24` (10) 9,540,
  `R_BFIN_PCREL24_JUMP_L` (13) 521, `R_BFIN_BYTE4_DATA` (18) 17,464. All are RELA; `readObject` refuses anything else, plus COMMON, weak, merge and COMDAT
  sections [C]. The kit's 7 `linked` machines: 105 objects, 690 globals, 9,994 relocations, 1,174,992 B. The whole kit: 2,893 globals, 53,953 relocations, 8.68
  MB of objects; the largest object is `troupe/voice.o`, 896,192 B.
- **Toolchain options** [M]: `-fPIC` changes nothing on bfin-elf (still LUIMM/HUIMM). `-mid-shared-library` emits `R_BFIN_GOT` and `BYTE2_DATA` and needs P5 set
  on every OS-to-module call. `-mfdpic` emits `GOT17M4`. Data pointers (BYTE4) need relocation in every option. Earlier work, since removed: a kit replay script
  with GNU ld parity and a 60-line relocation kernel (host-checked, never run) [C].

Detail: Design, Sizing, Reuse in [008-mod-assembly.design.md](008-mod-assembly.design.md); Stages in [008-mod-assembly.stages.md](008-mod-assembly.stages.md)

## Safety (Q8)
Assembly writes only a new library entry (`firmware/<id>/`: temp file, read-back SHA-256, `info.txt` last as in 003) and, on default change, `content/` through
the content switch. It never touches the boot partition, the bootloader, SPI, `bootloader.dev` or `user/`. Before admission, the device runs all of `core.mjs`'s
link and patch checks plus the stock-reconstruction check. It records its inputs (generation, selection, layout, package hashes) in the entry so `xyf_host.py`
can relink and compare bytes. A new entry is offered as **boot once**; the default changes only after the user says it worked. If it hangs or crashes, a power
cycle returns to the old default, and holding PLAY at power-on opens XY Forever's menu, which is still available because it runs from the boot partition before
any entry [C ARCHITECTURE]. The dev menu (COM) remains below that. All of this is recoverable without hardware [I]. A faulty mod still runs unsandboxed inside
the OS, as any open-xy build does today [C README].

## Rejected
Linking at every boot (boot-time risk and cost). An enable table in one big build (user: memory). PIC (measured: no PIC on bfin-elf without GOT, P5 setup and
data relocations anyway). Modules pre-linked into fixed slots (wastes the shared 2032 KiB; numbers and SDK tables still depend on the selection). A prebuilt
base `os.ldr` (above). Copying the 21.6 MB `kit.json` to the device (it holds wasm previews and base64).
