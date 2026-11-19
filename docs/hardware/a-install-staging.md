# Hardware: A install staging rehearsal

Part of [stock storage server requests](storage-server.md).

## RAM-only transport (#8, phase 1)

The product's `storage_server_a_stage.ipp` intercepts reserved handle `0x41535447` before
probes, engine latching, mounts or stock FD handling, even for inactive or malformed requests.
Known stock write-class framing (`0x2b`, existing bounded `write_start`/`left_poll`) carries a
RAM-service envelope; it never reaches a file executor or relaxes `decide_request`/`decide_engine`.
The existing peer `serve`/`reply` contract owns the pool request and replies without storage.

Each envelope is `{u32 magic=0x31534941, version=1, operation, token, offset, count, crc32, kind}`.
Begin (0) appends SHA-256; chunk (1) appends up to 4064 bytes; finalize (2), digest (3, offset
0..3 selects two computed digest words), abort (4) and preflight (5) carry only the header. Except digest's two
data words, replies are status plus progress/CRC. Newer versions refuse. Tokens strictly increase
within the boot; refusals, competing requests and expiry revoke ownership without clearing 8 MiB.
Preflight refuses an A write window before B closes readonly handles with op 9, so setup cannot
checkpoint an unexpected write window. Begin checks readiness again.
The lease expires after 10 s idle or 120 s total. Success reads A's digest and aborts its lease.

Simulator proof uses separate per-processor SDRAM files, A-only post-transfer corruption and
mount/raw-write/filesystem-change/executor counters. It proves dispatch and ownership behavior;
retained hardware DMA timing and real AES encryption remain outside this no-write rehearsal.
