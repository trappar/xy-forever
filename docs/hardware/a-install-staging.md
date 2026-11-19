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

## Qualification sequence

Use the checked checkout's host and protocol 1.22; the rehearsal payload version is 1.
`test_device_qualification_sequence_and_menu_cleanup` proves this sequence in the simulator,
including the existing B install/reboot before measurement, both real inputs, cancellation and
menu cleanup. Simulator assertions require zero deltas in mount calls, read-write mounts,
filesystem changes, accidental filesystem writes to boot partitions, raw boot changes and
sentinel executor hits. These counters are **not device telemetry**. Device evidence is A's
returned admission/hash, unchanged named state readbacks, cancellation/release and a responsive
menu; it does not establish unavailable A counter deltas or a complete NAND comparison.

Root coordinates the device session and keeper. Stop its existing keeper before opening the
port. After the full checks, review and device approval, install the checked product through the
shipping B writer and reboot separately when needed (`BUILD` is its verified build directory):

```sh
python3 xyf_host.py install "$BUILD" --yes-install
python3 xyf_host.py reboot --to menu --yes-reboot
```

Then, with `STOCK` the exact stock 1.2.1 `os.ldr`, use this bounded readiness and no-write sequence.
The `--uninstall` kind below only rehearses stock admission; it never invokes `boot.uninstall`.
For individual repetitions after readiness, the equivalent CLIs are
`python3 xyf_host.py dev-a-rehearsal "$BUILD"` and
`python3 xyf_host.py dev-a-rehearsal "$STOCK" --uninstall`.

```sh
python3 - "$BUILD" "$STOCK" <<'PY'
import hashlib, json, struct, sys, time
from pathlib import Path
import xyf_host as host
from xy_forever import a_rehearsal as rehearsal, usb_proto_ids as ids
from xy_forever.startup_ready import wait_startup_ready

product, _ = host.load_build(sys.argv[1])
stock, _ = host.load_stock(sys.argv[2])
image = json.loads((Path(sys.argv[1]) / 'manifest.json').read_text())['image']
dev, _, hello = host.connect(30)
try:
    assert ids.VERSION == dev.hello_info.proto == '1.22'
    assert dev.hello_info.answers(ids.A_REHEARSAL)
    assert dev.hello_info.keys['image'] == image
    wait_startup_ready(hello, lambda remaining: dev.call(
        ids.STORAGE_SPACE, struct.pack('<I', 1), timeout=remaining)[0], timeout=90)
    assert dev.call(ids.HOLD, struct.pack('<I', 0))[0] == 0
    names = ('state-a.txt', 'state-b.txt', 'config.txt')
    before = {n: dev.read_file('/yaffs2/xy-forever/' + n) for n in names}
    assert all(code in (ids.S_OK, ids.S_NOT_FOUND) for code, _ in before.values())
    for data, uninstall in ((product, False), (stock, True)):
        code, proof = rehearsal.run(dev, data, uninstall, deadline_ms=120000)
        print(json.dumps(proof), flush=True)
        assert code == 0 and proof['phase'] == 0
        assert proof['sha256'] == hashlib.sha256(data).hexdigest()
    assert dev.stage(stock) == 0
    extra = struct.pack('<II', 1, 120000) + hashlib.sha256(stock).digest()
    assert rehearsal.call(dev, 0, extra)[0] == 0
    code, state = rehearsal.call(dev, 2)
    assert code == 0
    deadline = time.monotonic() + 50
    while state['phase']:
        assert time.monotonic() < deadline, 'cancellation did not finish'
        code, state = rehearsal.call(dev, 1)
        assert code == 0
    assert state['result'] == ids.S_CANCELLED
    print(json.dumps(state), flush=True)
    # A is released. Refused zero-size stage.begin also forgets B's input token.
    assert dev.stage(b'') == ids.S_TOO_LARGE
    assert dev.call(ids.PUT_END)[0] == ids.S_STATE
    assert {n: dev.read_file('/yaffs2/xy-forever/' + n) for n in names} == before
    assert dev.call(ids.STATUS)[0] == 0
finally:
    dev.t.close()
PY
```

Stop on an unexpected result or lost link; no retry, fallback, reset or OS boot belongs to the
rehearsal. After a successful responsive end, root restarts the existing keeper using its private
session folder. The menu stays home with hold off; no staging file or device file needs deleting.
