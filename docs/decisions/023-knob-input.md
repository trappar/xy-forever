# 023: Reading the four knobs (turn and push) in the menu

Research record, 2026-11-14; no device action, nothing built. Listing: the private `analysis/bootloader-121-listing` (`linear121.ann.asm`). OS: stock 1.2.1
`os.ldr`, LDR blocks disassembled with `tools/blackfin/bin/bfin-elf-objdump` (scratch only, nothing kept). Tags: **[L]** read from code, **[C]** our code,
**[M]** measured, **[I]** inferred, **[U]** unknown.

## Answer

Both reach B through the PSoC (the backplane chip) on the link the menu already polls. **Pushes are ordinary keys** in the key stream we already receive and
drop. **Turns are a separate PSoC frame (type 0x45)** that the stock poll throws away, but every frame stays readable in the stock receive queue in RAM. Neither
needs a new stock call. Neither involves a GPIO or counter on B.

## 1. Stock 1.2.1 bootloader

- **Key enum** (name table `0x88a25584`, `KeyCount` 0x48 asserted in `88a25296`): 10..13 = `KEncClick1..4` [L]. The key diff `88a247c2` walks all 72 bits of
  each 0x42 frame and calls the callback at `ctx+0x813c` for every changed key, knob pushes included [L].
- **On the device:** the Test 020 report (`reports/test-020/play-held/ui-020-b.bin`, decoded by `census.py`) logs `EncClick1 down @4046ms / up @4177ms` [M]. So
  our `on_key` already gets knob pushes. `xyf_menu::key` returns `none` for any key it does not know (`target/xyf_menu.cpp`), so they are dropped [C].
- **Stock menus:** they use keys only. The handler `88a1f18a` switches on keys 6..21 (Track 1-4, EncClick 1-4, Sound 1-8; table `0x88a20704`). Factory reset
  asks "press white knob to confirm" (`88a1f16e`). The keyboard test (`88a20e20`, "press all keys and knobs") tracks the 72 keys in `0x8807cf7c`, with no turns
  [L]. No bootloader code decodes turns [L].
- **Frame types in the poll** `88a24922` (switch table `0x88a24c94`, inside our verified span): 0x42 → key diff; 5 → greeting/restart; 0x4d → USB-status log; 0,
  0x31, 0x32, 0x35, 0x37 → log; anything else → dispatcher `88a21456`. **0x45 → `88a249cc`, i.e. pop the next frame: dropped without a trace** [L]. Listing 0x45
  as its own case suggests the PSoC sends it in the bootloader too [I, medium-high].
- **Receive queue** (te_utils `Queue.h`): `ctx` = `0x88a522c8`, queue `Q` = `ctx+0x34` = `0x88a522fc`. It holds 128 slots of 0xfd bytes. The read index is at
  `Q+0x7e80` (`0x88a5a17c`) and the write index at `Q+0x7e84`; when they are equal the queue is empty (`11a06cae`). A pop (`11a0728e`) copies slot
  `Q + read*0xfd` to `0x8807d5c8`, advances the read index (`11a05740`) and **does not clear the slot** [L]. Slot layout: `[route][seq][type][len][payload]`.
  PSoC replies have route 0x48 (017) [L].

## 2. Stock 1.2.1 OS

- `command_handler_kb.cpp` (PSoC) main loop `8d5b2710`. Frame type 0x45 (`8d5b2752`) is a list of 3-byte records `[id][value int16, big-endian]`. **ids 1..4 →
  `0x8c0b64a0(encoder = id-1, delta)`**, which checks `0 <= encoder < 4` and keeps per-encoder state at `0x08024c8c`. A call to `0x8c0b630c` follows. id 5 →
  `8d4db5a8`. ids 6..7 and 8..13 are other values [L]. What 5..13 mean is [U]; id 5 may be the volume control [I, low].
- So the OS reads the encoders from the PSoC, not from B's pins. B has one hardware quadrature counter (CNT0) and could not count four encoders
  [I, BF70x data sheet]. Whether the OS sends the PSoC a command that turns 0x45 reports on is [U]: no such command was looked for.

## 3. Options, ranked

| | What | Stock calls / approval | Can go wrong | Safe test |
|---|---|---|---|---|
| **(a) pushes** | Map keys 10..13 in `xyf_menu::key` and `mods_io::key` to act like PLAY | none; behaviour only | none on the device: a key that does nothing | already seen for knob 1 (Test 020); a build shows each knob in the event log |
| **(b) turns, read the queue** | After each stock `poll()`, decode the slots from our own cursor up to `[Q+0x7e80]`. Accept a slot only if route 0x48, type 0x45, len % 3 == 0 and len ≤ 249. Add each id 1..4 delta to that knob's sum. If an index is ≥ 128 or moves backwards (a greeting restart), set the cursor to the read index | **no stock call**; plain DDR reads of stock RAM (like the key reply at `0x8807ce7e`). a new kind of read (see Approval) | wrong layout: garbage deltas move the list, nothing else; more than 128 frames between polls: a lost step | read-only probe: show `r`, `w`, the last 0x45 bytes and the four sums on screen and in `status`, turn each knob; no behaviour uses it until then |
| (c) own frame pump | Replace `poll()`: pop with `11a0728e`, 0x42 → `88a247c2`, 0x45 → ours, other types → `88a21456` | **new**: `11a0728e` (L1, allowed by address only), `88a247c2` (inside span `0x88a24500`), `88a21456` (new span; it can reach the SPI metadata writer, TFW-AUTH §3) | replaces the greeting/restart handling; keeps the dispatcher risk | as (b) |
| (d) talk to the PSoC | Only if (b) shows no 0x45 frames: find and send the OS's "enable" request | **new**: UART write `11a04b42` (CP001 call; 015 Q5); a new message, never a firmware write (015/017 rules hold) | a request the PSoC does not expect; stock recovers the link on greeting [I] | not designed; first read the OS for the request |

(b) sends nothing to the PSoC and adds no UART reads. It only reads what the stock handler has already received, so it does not conflict with 015/017.

## 4. Recommendation and open points

- **Do (a) now.** **Do (b) as a two-step device checkpoint:** first the read-only probe, then list control. Drop (c). Keep (d) as the fallback only if the probe
  sees no 0x45 frames.
- **Approval:** none for a new stock call. Reading the stock receive queue (RAM, no call) is a new kind of read: accepted for knob input only.
- **Open questions:** do all four knobs scroll lists, or only one? Does Shift + push act like Shift + PLAY (set the default)? Should a push of the held boot key
  be ignored, as PLAY's is in `key_ui.cpp`?
- **[U]:** whether the PSoC sends 0x45 while the bootloader runs (the probe settles it); how many units one detent gives and which sign is clockwise (the probe
  shows raw values); pushes 2..4 on the device (same path as 1 [I, high]); whether the read index wraps at 128 (0x7e80 = 128 × 0xfd [I]; not read in
  `11a05740`); what ids 5..13 are.
