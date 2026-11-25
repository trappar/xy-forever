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
frozen on its last frame, no input, no crash screen, the OP-XY gone from the bus. A core-timer tick every 0.5 s
(IVG6, tried as a hang detector) made it go away, and the same build without the tick froze again: so the core
was likely stuck in a way only an interrupt that does not come through the system bus could end. That fits the ADSP-BF70x
silicon anomaly 19000058, "System MMR Reads Can Cause Core Hang", whose workaround (NOPs on both sides of each
system MMR read, the load 8-byte aligned) the stock code carries and our GCC-built code did not. B's menu loop
reads the display DMA's status, a system MMR, at every frame; that read now carries the workaround
([code rules](../architecture/code-rules.md)). Confirmed only once a build with it, and without the tick, reads
through repeatedly with a host polling.
