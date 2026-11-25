# Hardware and stock-code facts: when B stops

Part of [Hardware and stock-code facts](../HARDWARE.md).

When USB stops answering (#314), the screen tells what stopped. Replies that cannot be written into the CDC put
"USB: replies are not getting out. Reconnect the cable." on the activity line until one gets out (STATUS: USB1
`replies_lost`). On the progress screen, STOP shows "Cancelling..." only while B's menu loop runs: no change and no
`W<code>` ([where codes](devices.md#where-codes-114)) means B itself stopped (its loop or its interrupts), not only USB.

The stock PSoC poll, which the menu loop calls every pass, can stop B for good: a power status frame (0x31, 0x32)
with status 0x14, or one going from 0x10 to 0x16, makes its power handler shut down (USB stopped with its PHY down,
so the OP-XY leaves the bus) and wait for the power to go; its command handler can shut down too
(`target/knob_input.hpp`). So the menu draws any such frame top left before that poll takes it: `P31:16 off` (type,
status; "off": the power handler shuts down on it) or `P<type>:<byte 4>` (a command frame). STATUS keeps the last 16
non-knob frames and the power handler's stored status (diag.status block PSC1, `psoc_frames`).

B has also stopped as a whole during the on-device content read with a host polling STATUS (#314): the screen
frozen on its last frame, no input, no crash screen, the OP-XY gone from the bus by the next replug (right after
one freeze its serial port was still there, unanswered), and nothing left to read. So the
evidence goes on the screen, which keeps its last frame:

- **The stop screen.** The core timer (IVG6, unused by the stock code) is a dead man's timer while the menu loop
  runs: every pass of the loop and every poll of a bounded wait winds it back, so it interrupts only when
  nothing has moved for 3 s, plus once on purpose 1 ms after the loop starts, the proof that it can. It then logs (STATUS
  block STL1, `stalls`) and draws "Stopped": IPEND, the interrupted instruction, RETS, the frame and its callers,
  the message a stock assert left at the frame, the wait, the loop's last step (`usb_service::crumb`). A stock assert ends in the ROM's IDLE
  loop (`0803dfcc`) at the level that asserted; the stock interrupt wrapper (`11a015d8`) has pushed RETI by then,
  so IVG6 still gets in. It never resets or writes, and it returns: a loop that goes on draws over it. The install's
  stock writer stops the timer. An earlier tick every 0.5 s seemed to change how often B froze; this one never ticks
  while the loop moves.

A freeze with no stop screen leaves: the core hung (anomaly 19000058, a system
MMR read; ours carry the workaround, [code rules](../architecture/code-rules.md)), interrupts masked for good, or
the display's DMA no longer ending.

The stock interrupts do nest: the stock start sets SYSCFG to self-nesting and dynamic branch prediction (`11a0009e`,
0x306), and the wrapper pushes RETI. On silicon revision 1.0 that is anomaly 19000054 (lost nesting state, unfixed in
the stock dispatcher `11a01570`); revision 1.1 does not have it. The revision of the OP-XY's processor is not known.
