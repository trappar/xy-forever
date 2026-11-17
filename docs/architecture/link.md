# XY Forever architecture: the link between the processors

Part of [XY Forever architecture](../ARCHITECTURE.md).

## The link between the processors (stock SPI request protocol, reopened at cold time)

1. **Handshake** (proven, Test 023). B sends stock request 1 (reply `0xfedebeda`),
   request 10 (reply `0xedcba987`), then release 7.
2. **Held link** (proven, Test 024). B sends op-10 keepalives every 250 ms; A treats any new request
   header in its RX buffer (`0x11b000c0`) as activity (idle window 3 s; 10 s for the product). Product
   sessions have no cap (`peer_services::no_cap`): the cold millisecond clock (`target/xyf_clock.hpp`) is
   wrap-safe, so the menu and USB stay up indefinitely. Before 086 the old formula overflowed after ~524
   s, which is why sessions were capped at 300 s. Op-10 replies are computed by the stock ISR, so A → B
   data always uses the image-transfer layer.
3. **Commands** (`'CM' << 16 | item << 8 | seq`). Two transports:
   - **Requests** (boot session; proven since 068). B sends the value as
     argument 0 of a stock op 0x18 request (`Stock121PeerSession::ask`). The stock
     ISR only queues such a request, so it waits until A's loop takes it. A answers
     `~value` itself and acts on the command before it polls again. B may therefore
     send a command while A is busy (mounting, reading an image): it is answered
     late, never lost. B waits up to 15 s (`xyf_boot::command_ms`).
   - **Op-10 arguments** (keepalives; commands of the removed v2 flow; proven). A only
     samples the header buffer, so a command is lost if another header follows
     before A polls again. That is safe only while A's loop is polling. It was the
     cause of the 064/066 link failures (HISTORY, Diag 076), which is why the boot
     session moved to requests.
4. **Image transfer** (proven). A freezes an admitted image (or a small blob) and serves it through stock
   partition/sector ops 0x18/0x11/0x12; B receives and verifies it. Product boot transfers carry no
   SHA-256 (087, user decision): the image is identified by size and CRC-32 (`info.txt`), and each copy
   is CRC-checked once (089): A's file read at CHECK, B's received bytes as they arrive, each boot copy
   at hand-off. Any failure means no boot (menu, or the halt screen).
5. **Storage server** (proven, Tests 027–050, 097–139). A runs B's file requests with the stock
   executors through one file engine (build 155; `target/file_engine.cpp`, decision 009):
   every request passes `decide_request` and its path policy `decide_engine`, which B also applies
   before it sends. B's timed client (build 158; `target/file_client.cpp`) uses the stock builders on
   the session's one slot, never the stock RPC; every wait bounded (5 s, 45 s for a change: then `link`;
   still out after 60 s: link lost, B stays at the menu: item 6).
   Changes share one read-write window (build 163): A opens it on the first change; B closes it (op 9)
   8 s after its last write and before install, set default, CONTINUE, REBOOT; A after 10 s idle, before
   other command items, when B is gone; A then rescans, B fetches the catalog. Refused: removing or
   renaming away `/yaffs2` or `/yaffs2/bootloader.dev` (also via links), anything outside `/yaffs2`, bad
   paths, the raw boot-partition layer (install mode only), all while a boot partition may be selected.
6. **Lone restarts** (record 016; built 159): a restart of one processor becomes a restart of
   both, in the stock order (A's bootloader serves B's boot). A never answers B's stock bootloader: its
   link open (op 10 `0x12345678`) after B's first command, or a boot request (select, sector read) with
   nothing frozen outside install mode, is held unanswered; A closes storage, restarts, and its bootloader
   answers the held request (Test 108). A also follows when B is silent past its idle window. Since 164
   (record 022) B never resets itself on a lost link (menu, USB, `link`), A has no guard of its
   own and never restarts by a timer of its own; keepalives are plain op-10 commands.
