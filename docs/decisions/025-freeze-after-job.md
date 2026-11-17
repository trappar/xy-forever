# 025: The freeze after a mod job

Research record, 2026-11-15; no device action. Evidence: the first mods-apply session's freeze log
(build 172; the maintainer's, not shared). Signature: job done, then the next HELLO (0.3 s later) unanswered, USB still
enumerated, menu frame kept, dead until a power cycle; job output complete. Not seen in six later runs.
Tags as in 022: [C] code, [M] measured, [I] inferred, [U] unknown. Line numbers are build 172's.

## Hypotheses

1. **The guard (B's watchdog, A's idle follow).** Against [C]: `guard_disarm` (usb_service.cpp 215) stops
   WDOG0 and restores SEC source 3; `guard_kick`, `guard_count_set` do nothing while `guard_on` is 0, so no
   counter runs after `guard off` and no stale armed flag exists. B's only reset paths are `reset_self`
   (REBOOT/restart items, dry run, `guard_expired`); none is reachable from the job, `job_finish` or the session
   code. A's guard does not exist in 172. A's `a_follow` (file_boot.cpp 361, 501, 730) restarts A alone when B's
   bootloader asks (F1) or when B is silent past A's idle window (10 s, 347) after commands. B's keepalive goes
   out every 250 ms from `tick()` (file_boot.cpp 141, 995), but `tick()` runs only between job passes (pass
   <= 98 ms [M]) and not inside a blocking wait, so A cannot see 10 s of silence [I]. A lone A restart also
   leaves B's USB answering (it only loses the link), which does not fit the signature. Verdict: unlikely [I].
2. **A processor exception on B.** XY Forever installs no exception or hardware-error handler on either
   processor [C: no EVT write in `target/`]; the stock one runs. What it does is [U]. A stock handler that
   spins with interrupts masked fits the signature exactly (USB stays enumerated by the controller, no HELLO
   answer, frame kept), as does a lone B reset (022). **Stack overflow is refuted:** worst-case static depth
   of the module (build 172 `module.asm`, LINK/push/`SP +=` per function along CALLs) is 2.8 KiB from
   `xy_forever_cold_run`; `mod_job::step` adds 1.2 KiB, a job file callback 0.5 KiB; even with stock frames
   and interrupt nesting that is far below 16 KiB, and an overflow would land in the unused code window
   `0x8ab28884..0x8ab80000` (module end, `module.map` 1891), not in code or data [C/I]. The work region
   `0x8b000000..0x8b800000` is B's alone (cold_layout.hpp, `check_memory_map`); no reuse after the job [C].
3. **USB during job end.** In 172 `job_finish` closed A's write window with a blocking, bounded wait
   (`settle_writes`); 173 made it non-blocking. A HELLO arriving then is queued by the stock
   receive path and answered after the wait [C]; HELLO needs only B. A wait that never ended would show as
   "busy" for file commands but HELLO would still be answered after the bound [I]. Verdict: unlikely; 173
   removes the window anyway.

## What is unknown, and what makes the next one diagnosable

- The stock exception handler's behaviour, and whether B halted, spun or reset. A sure split needs a sign of
  life from B after the freeze: an own exception handler that draws cause (SEQSTAT) and address (RETX) and
  halts. Writing EVT3/EVT5 is a new kind of change (not a stock call, but it replaces the stock vector): it
  needs the developer's explicit yes and one device test; not built here.
- Built here: `status` reports B's live stack high-water mark (`stack_used`, bytes below `XYF_STACK_END`).
  Until now it read 0 because only the census record set it, after the menu loop ends (key_ui.cpp 607). If a
  later freeze follows a high mark, hypothesis 2 (stack) comes back; a steady low mark keeps it refuted.
  Code: `target/stack_mark.hpp`, `usb_service::status`/`stack_watch`, key_ui after `paint_stack`; test
  `tests/test_stack_mark.py`. Not yet compiled for the device or seen on it.
- A cheap next check on the device: after any long job, `status` then a file command within 0.3 s, ten times
  (the one thing the failed run had that a-c did not). Build 173 or later.
