# 033: Publication audit (plan item P.2, research)

Research record, 2026-11-16; read-only, no device action. Scope: every tracked file of `xy-forever` (491) and
`xy-forever-web` (16), by `git ls-files` and `git grep` counts. The per-file list stays private with the
maintainer.

**Question.** Before the tree is exported as a fresh private repo for other Open-XY developers, what happens
to each tracked file: keep, scrub, drop, or local-only (gitignored, template committed)?

**Answer.** xy-forever: keep 394, scrub 93, drop 3, local-only 1. xy-forever-web: keep 12, scrub 4. No binary
file is tracked. No key material, vendor bytes or decompiled code.

1. **Personal data.** One PLAN.md line (MP section) has the maintainer's name, a personal email and
   home-relative paths; P.7's export path is home-relative. Nothing else in either repo (40+ hex strings are
   digests, pins, RFC 6979 vectors or synthetic keys). Fix in P.4, with a leak test.
2. **Private references** (251 in 89 files). Through `xy_forever.paths` / `XYF_PRIVATE`: keep (~30 files).
   Hardcoded `../private`, bypassing `XYF_PRIVATE`: fix in code (capture_session.py, open_xy_link_codex.mjs
   and test, test_sim_mtp_session.py, clients/ts device-browser-test.ts, web e2e). Session or build defaults:
   fix (mod-package path in device_pass.py/mod_boot.py, test_entry.py session, fixed build names in
   test_conformance/test_ts_client/test_simulator/simulators.ts, dated upstream clone in
   test_mod_link/test_modkit, preview dirs in test_ui_mods). Evidence pointers in 11 code comments and ~30
   docs: cite the record or HARDWARE section instead. Fixtures: stock `os.ldr`/`bootloader.ldr` feed the
   simulator, builder and most tests; product builds and mod packages are generated from them plus the
   pinned upstream open-xy commit and public kit. Official `.tfw` files, update keys, decrypted packages,
   content manifests and BLE images do not come from stock files; their tests must skip. The bootloader
   listing serves only `re/` tools.
3. **Maintainer voice** ("the user": 326 in 91 files). (a) End user or operator in product text
   (PROTOCOL, protocol/v1.json, clients, "user area"): keep. (b) Dated decisions and approvals (99 in 36
   files, ~110 quotes, mostly PLAN): neutral project rules citing the record; allowlist comments become
   facts plus "changing this list needs the developer's explicit yes". (c) Agent workflow aimed at one
   person (AGENTS, ORCHESTRATION, OPERATING, SAFETY, device scripts): "the developer at the device".
   (d) Session narrative and rationale quotes (PLAN, ORCHESTRATION, records 016/021/022/029): cut.
4. **Commit hashes.** ~130 references to this repo's commits, all in docs; the larger 7-8-hex count is mostly
   stock addresses (keep). PLAN (~60): cut with Done and notes. Record 026 (20): drop. Other records: drop
   "against `hash`", use item IDs. GUIDE, MODS-DEV: a version, not a commit. Upstream open-xy hashes stay.
5. **Working-state docs.** PLAN: keep, scrub; session state and device queue go to the local status,
   "decided by the user" to GOALS Decisions, Done to one line. STATUS: local-only with a committed
   `STATUS.template.md` (budget test and AGENTS accept either). HISTORY: lessons only, no Evidence section.
   ORCHESTRATION, OPERATING, GOALS, SAFETY, records: keep, neutral voice.
6. **Litter.** Drop the open-xy link replay experiment (`scripts/open_xy_link_codex.mjs` and its test:
   superseded by modkit/modlink, private-only fixtures, a tool name in the file name) and record 026.
   Record 001's diagnostics stay. Nothing generated was committed by mistake.
7. **Vendor content.** Digests, CRC spans, addresses, short stock log strings, behavioural simulator
   stand-ins; stock files are read at run time only. Nothing to drop.
8. **Agent-memory rules** (29). Covered: 16. Move 10: OPERATING (test timeouts, automated browser tests,
   the developer boots test firmware), ORCHESTRATION (device checkpoints first, decisions asked with a
   recommendation, standing approval for routine installs), SAFETY (prevent hangs, no lone-processor
   recovery), AGENTS (fresh upstream open-xy only, recommend rather than defer, branch per change).
   Personal, kept out: 3 (attention sound, dev-menu fallback tolerance, MTP fast iteration).
9. **Workspace AGENTS.md.** Layout and tools to ONBOARDING (configurable); "resume work" to AGENTS as
   "Continue development"; host-authorisation scope to a SAFETY rule plus a per-developer grant; cleanup
   logging to OPERATING. The paused port folders stay out.

**Build split for P.3-P.5:** (i) hardcoded private paths and session/build defaults; (ii) fixtures and setup
(empty private folder: skips only); (iii) voice in code and protocol/v1.json, regenerate PROTOCOL.md and v1.ts;
(iv) voice and hashes in reference docs and records; (v) PLAN/STATUS/HISTORY split, template, leak test;
(vi) memory and workspace rules into the docs and ONBOARDING; (vii) web repo paths.

**Rejected.** Dropping all decision records (they stop repeated research); committing STATUS.md as is (one
device's state); approvals as quotes (they are per developer); rewriting stock addresses (notes, not vendor).
