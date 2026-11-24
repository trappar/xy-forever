# The Install page

The web app's **Install** page (#178, `src/pages/install.tsx`, `src/install/`), in full; the summary is in
[the web app's README](../README.md#pages).

It installs XY Forever on a stock OP-XY, no other apps. Its flow, wording, MTP FAQ and "start fresh
with OS 1.2.1" path are Open-XY's builder flow (`src/install/flow.ts`, `content.ts`), and its te-boot (Web MIDI) and dev-menu MTP
(WebUSB) code is Open-XY's `ferret.mjs` and `mtp.mjs` (`src/vendor/open-xy/`, MIT, notices kept; the Ferret copy has
no disable command). The page reads te-boot's identity and refuses a 1.2.5 bootloader or unknown firmware (older than
1.2.1: the official 1.2.1 update, named exactly), switches the dev menu on (never off), then copies our `os.ldr`:
over WebUSB on a Mac (after checking that the stock `upgrade/firmware/blackfin/bin/os.ldr` is there, by its stock 1.2.1 size, and
the free storage; read back), or downloaded and dragged into Explorer on Windows (after the owner finds that stock
file in Explorer, about 2,318 KB). Track 5 and a power-on are the
owner's. The page never reads or moves the stock OS: at its first start XY Forever adopts that file on the device
(hashed there, copied into library entry `opxy-121`, made the default, then booted; `docs/storage/jobs.md`). The
page reads where that setup stands over Web Serial (`src/install/finish.ts`: `job.status`, the entry, the default):
while it runs it shows it, a refusal shows the device's own words (removing the entry is advised only when the
device says it is incomplete), and once the entry is there and the default names a library entry (it, or one with
mods) the page hands
the connection to the app and opens **Mods** with a dismissible "XY Forever is installed" notice. The last step tells
the owner once that holding PLAY at power-on opens the XY Forever menu (the plain first start boots OS 1.2.1, so the
page meets XY Forever there). The pure checks: `src/install/checks.ts`. The choices a stage needs (the computer, the
file sent, a refusal) are kept in `sessionStorage` for a reload (going Back to the copy step sends the file again);
a reload at a te-boot stage asks te-boot again. At every stage (on load, on each stage change, when a serial port
appears, and every few seconds at the last stage) the page looks for an OP-XY running XY Forever on ports Chrome
already granted, never opening the chooser unasked, and jumps ahead when it answers; **Find my OP-XY** (a click,
which may open the chooser) does the same. It never looks while a te-boot check over Web MIDI or the MTP copy is
under way, only once that ends (te-boot is MIDI only; the serial "OP_XY Boot" is XY Forever's own fallback service).
Either way it follows the setup as above.

When the site has a canary ([release](release.md#canary)), the first stage offers Stable (the default) or Canary,
and both stages before a copy show the chosen release's version and image (a canary's date and commit too). The
choice, its pin, is kept with the others and stays fixed for the rest of the install: a reload or a newer deployment
never swaps the image, and bytes that differ from the kept pin are refused.
