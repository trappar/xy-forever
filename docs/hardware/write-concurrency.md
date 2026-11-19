# Stock write bodies and concurrency (1.2.1)

Part of [Hardware and stock-code facts](../HARDWARE.md); see the
[stock storage server requests](storage-server.md) for request classes and other contracts.

A's four request slots are not four independent write buffers. The stock write receiver
`889f8c76`, at `889f8ccc..889f8cdc`, sets request `+0x20` to the fixed buffer `0x8803f6e0`
and receives the body there through `889f5f4a`. Once received, `889f8ca4..889f8cb6` finishes
the receive and publishes the request through `889f66ce`. The write executor later passes
that same pointer and request `+0x24` to yaffs write (`889f6e28..889f6e32`). Publication
keeps a pointer, not a private copy of the body; `889f66ce` also asserts that its one pending
request pointer is empty before setting it.

The master's request queue (`11a0060c`) proves that it can queue objects; it does not prove
that A keeps each object's body until its executor finishes. This focused static check did
not establish backpressure that prevents a second write body reaching the shared buffer
while the first is in use. Simply adding another pending write on B can therefore overwrite
data still in use on A. Keep one write request outstanding until independent body ownership
or the complete serialization contract is established. The simulator serializes A's body
placement until its current request answers (`sim/link.cpp: a_service`); it cannot prove
the stock interrupt's backpressure.

The stock OS's two 16 KiB MTP upload buffers overlap USB receive with one writer task;
they do **not** mean two simultaneous storage requests. The writer (`8d5c112c`) calls
`fwrite` at `8d5c1170` and releases its buffer only after it returns. The remote write loop
`8c0c188c` splits data into at most `0x1000` bytes (`8c0c18de`), builds op `0x2b`, and waits
through `8c0c3a88` -> `8c0c2a0c` before its next piece (`8c0c190a..8c0c1928`). This is
static call-path evidence, not a measurement of OS task concurrency.
