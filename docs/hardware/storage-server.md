# Hardware and stock-code facts: stock storage server requests

Part of [Hardware and stock-code facts](../HARDWARE.md).

## Stock storage server requests (role 0, as MTP uses them)

Classes by vtable; A's executor runs the op and replies. **Some classes always send a
data body, and B waits for it without a timeout**, so a refusal must have the same shape.

| Class (vtable) | Ops | Reply body |
|---|---|---|
| partition `0x11907694` (`889f76ca`) | 8 mount, 9 unmount, `0x2c` lseek, `0x2d` close, `0x30` closedir, `0x34` fsync, `0x36` truncate (by descriptor); 4 / `0x14` / `0x18` = raw partition layer | none |
| path `0x119076b4` (`889f6fea`) | `0x21` access, `0x25` unlink, `0x26` mkdir, `0x27` rmdir, `0x28` yaffs sync, `0x29` open (flags at +8), `0x2e` opendir, `0x35` set times | none |
| query `0x11907388` (`889f72e4`) | `0x22` stat, `0x23` statfs, `0x31` free space (64-bit) | always: `0x34`, `0x0c`, 8 bytes |
| sector `0x11907348` (`889f6b7c`) | `0x2f` readdir (result: A's dirent address, 0 at the end, `889f6c02`; never 1); `0x11` / `0x12` boot-partition sector reads | always: `0x10c` (also at end of folder); `0x200`·n |
| read `0x11907368` (`889f6cd6`) | `0x2a` read | streamed |
| write `0x11907710` (`889f6dca`) | `0x2b` write; `0x15` / `0x16` boot-partition sector writes | none |

There is no rename and no format request. A's slave interrupt answers the link handshake
(ops 1 and 10) by itself in every mode, so a B that restarts alone talks to the old A.

See [Write bodies and concurrency](write-concurrency.md) for stock write-buffer ownership
and serialization limits.

Restart order, request flags and other stock contracts: [stock contracts](stock-contracts.md).

## Protected inbox paths

A's mutation guard compares the marker's identity and walks the resolved parent directories of a path
before changing storage. This catches aliases directly into inbox subfolders, not just aliases to the
`upgrade/` root. YAFFS follows directory links before interpreting internal `..` (1.2.1 listing:
`889fa246` / `889fa258`; a final `..` uses the parent at `889fa300`). The guard uses the already-admitted
local stat call, never a new stock entry; client paths still reject literal `.` and `..` components.

The walk is bounded at 120 parent steps, enough for every direct client path's 239-byte limit. An alias
hiding deeper ancestry, a failed parent stat or a missing mount root refuses the mutation. This is not
the six-folder creation cap: existing deep files remain deletable when ancestry fits that bound.
The extra stats have not been timed on hardware.

**Final links (#6):** stat follows a final link but exposes no parent, and a writable open follows it (stock open
`889fbbe4`: lookup `889fa2be`, then FollowLink `889fa134`). For writable opens only, A's
`final_link_reaches_inbox` (the one caller of the admitted lookup `find(relative, path, depth, equivalent, &parent,
&notdir, &loop)`) repeats that resolution: each link text copied first, relative ones from the link's own parent, at
most 6 links as stock, then the target's parents by object (`yaffs_obj` parent `+0x1c`, type `+0x64`: 2 link,
3 folder; link text `+0x68`). A missing target is judged by the folder that would hold it. A loop, a bound reached
or a span mismatch refuses. Unlink, rmdir and rename act on the link entry, so a link into the inbox can be deleted.
Hardlinks resolve through `equivalent`; the simulator does not model them.

## Read-only boot-partition diagnostic (#7)

The [probe protocol and measured A preconditions](boot-partition-probe.md) cover developer
command `0x64`, its read-only storage gate, restoration and the design go-ahead for #8.

The [RAM-only A staging transport](a-install-staging.md) specifies #8 phase 1: existing owned
request framing, exclusive RAM ownership and no storage writes.
