# 008: On-device mod selection (stages)

Detail of [008-mod-assembly.md](008-mod-assembly.md).

## Stages
1: one kit generation, catalog mods, 1.2.1, no extensions: choose on the device; unchosen mods use no memory beyond ~0.45 KB of SDK table rows each. 2:
extensions as toggles; keep the previous assembled entry for rollback. 3: our own table-driven SDK tables (rows in each mod's package, gathered by the linker),
so packages stop depending on the collection, the 0.45 KB rows go, and adding a mod needs no re-export. 4: other OS versions, once a profile exists.

| Step | Done when |
|---|---|
| build: mod packages (host) | kit → `base` + one package per machine (contents above); recombining them gives a kit whose `core.mjs` links match the original's byte for byte for the `linked` selection and 10 others; tests |
| build: native linker | `native/mod_link.*`: module, globals and layout byte-identical to `core.mjs` for every subset of the 7 `linked` machines and 20 random catalog selections; numbers equal `numbers.json`; every `core.mjs` rejection reproduced |
| build: native image + content | patched `os.ldr` (profiling off) and `op1_factory.json`, svg and presets byte-identical to `patchFirmware`/`buildAssets` (private stock fixture); self-check rejects tampering |
| #24 build: assembly job | cache layout, scratch memory-map entry, step-bounded job on A, temp/read-back/`info.txt` admission, device layout persistence, serial `MODS_APPLY`/`MODS_STATUS`; simulator: a power cut at every write leaves library and layout valid |
| build: Mods page | list with toggles and a memory meter (2032 KiB), Apply → progress → boot-once offer; `ui_preview.py` screens; `xyf_host.py mods push/apply/verify` |
| device safe: dry run | fm2op + ringmod assembled on the device, pulled back, byte-identical to `core.mjs`; time and RAM recorded; nothing booted |
| device risky: first boot | that entry booted once with its content (the content switch); user: booted, mods listed; a power cycle returns to the default |
| device safe: toggle round trip | add karplus, drop fm2op, assemble; fm2op's numbers still held; its content removed; the old default untouched |

Needs: mod packages after #22; the assembly job after M2's file engine, the linker and the image; first boot after the content switch.

**Unproven:** write speed and job time on the device; the scratch region; stock behaviour with catalog entries for absent machines (decision 018); licences of
most community machines. **Open questions:** (1) may version 1 take its mod packages from open-xy's own kit (published catalog; own mods by re-exporting the
whole set on the computer)? (2) community catalog mods in the list, or Open-XY's own first? (3) the website's mode (per-voice profiling off)? (4) extensions
(capture, DJ filter) now or in stage 2?
