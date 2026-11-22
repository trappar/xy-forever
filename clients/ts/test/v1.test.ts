// Checks the generated protocol module (src/v1.ts) against the spec's own test data: every golden
// frame through the typed codecs, the frame encoder and the parser, and every stream vector through
// the parser. Run: node test/v1.test.ts (Node 22.18+/23.6+ strips the types). No device, no I/O.
// `pnpm test` runs it before Vitest.
import assert from 'node:assert/strict';
import * as v1 from '../src/v1.ts';

type Codec = {
  encodeRequest(values?: unknown): Uint8Array;
  decodeRequest(payload: Uint8Array): unknown;
  encodeReply(reply: unknown): Uint8Array;
  decodeReply(payload: Uint8Array): { ok: boolean; status: number; fields: unknown };
};
const codecs = { ...v1.codecs, ...v1.dev } as unknown as Record<string, Codec>;
let checks = 0;
function check(what: string, test: () => void): void {
  try {
    test();
    checks++;
  } catch (error) {
    console.error(`FAIL ${what}`);
    throw error;
  }
}

function layoutOf(g: v1.GoldenFrame): v1.Layout {
  if (g.direction === 'event') return v1.EVENTS[g.message as v1.EventName].payload;
  const command = v1.COMMANDS[g.message as v1.CommandName];
  return g.direction === 'request' ? command.request : command.reply;
}

// Golden values as the codecs take them: bytes and rest fields are hex in the spec.
function valuesOf(g: v1.GoldenFrame): Record<string, v1.Value> {
  const values: Record<string, v1.Value> = {};
  for (const f of layoutOf(g).fields) {
    const value = g.fields[f.name];
    if (value === undefined) continue;
    values[f.name] = f.type === 'bytes' || f.type === 'rest' ? v1.fromHex(value as string) : value;
  }
  return values;
}

check('crc32', () => assert.equal(v1.crc32(new TextEncoder().encode('123456789')), 0xcbf43926));
check('USB ids: the product composite (temporary pair) first, then the stock serial identity', () => assert.deepEqual(
  v1.USB_FILTERS, [{ usbVendorId: 0x1209, usbProductId: 0x0001 }, { usbVendorId: 0x2367, usbProductId: 0x0021 }]));

for (const g of v1.GOLDEN) {
  check(`golden: ${g.name}`, () => {
    const values = valuesOf(g);
    let type: number;
    let payload: Uint8Array;
    if (g.direction === 'event') {
      type = v1.TYPE[g.message as v1.EventName];
      payload = v1.events[g.message as v1.EventName].encode(values as v1.LogEvent);
    } else {
      const codec = codecs[g.message]!;
      type = v1.TYPE[g.message as v1.CommandName];
      if (g.direction === 'request') {
        payload = codec.encodeRequest(values);
      } else {
        const status = v1.STATUS[g.status!];
        const fields = Object.keys(g.fields).length || status === 0 ? values : null;
        payload = codec.encodeReply({ ok: status === 0, status, fields });
        type |= v1.REPLY;
      }
    }
    const frame = v1.encodeFrame(type, g.seq, payload);
    assert.equal(v1.toHex(frame), g.hex);
    const parser = new v1.FrameParser();
    const [found, ...more] = parser.feed(v1.fromHex('00ff58' + g.hex));
    assert.deepEqual(more, []);
    assert.deepEqual([found!.type, found!.seq], [type, g.seq]);
    if (g.direction === 'event') {
      assert.deepEqual(v1.events.log.decode(found!.payload), values);
    } else if (g.direction === 'request') {
      assert.deepEqual(codecs[g.message]!.decodeRequest(found!.payload), values);
      const command = v1.COMMANDS[g.message as v1.CommandName];
      if (!command.request.ignored) {
        assert.throws(() => codecs[g.message]!.decodeRequest(v1.fromHex(v1.toHex(found!.payload) + '00')));
      }
    } else {
      const reply = codecs[g.message]!.decodeReply(found!.payload);
      const status = v1.STATUS[g.status!];
      assert.equal(reply.status, status);
      assert.equal(reply.ok, status === 0);
      assert.deepEqual(reply.fields, Object.keys(g.fields).length || status === 0 ? values : null);
      // Replies are decoded leniently: a later 1.x may add fields at the end.
      if (v1.COMMANDS[g.message as v1.CommandName].reply.extensible && reply.fields !== null) {
        assert.deepEqual(codecs[g.message]!.decodeReply(v1.fromHex(v1.toHex(found!.payload) + 'aabbccdd')), reply);
      }
    }
  });
}

for (const vector of v1.STREAMS) {
  check(`stream: ${vector.name}`, () => {
    const expect = vector.frames.map((f) => ({ type: f.type, seq: f.seq, payload: v1.fromHex(f.payload) }));
    const parser = new v1.FrameParser();
    assert.deepEqual(vector.chunks.flatMap((c) => parser.feed(v1.fromHex(c))), expect);
    const whole = v1.fromHex(vector.chunks.join(''));
    assert.deepEqual(new v1.FrameParser().feed(whole), expect);
    const bytewise = new v1.FrameParser();
    assert.deepEqual([...whole].flatMap((b) => bytewise.feed(Uint8Array.of(b))), expect);
  });
}

check('hello', () => {
  const [product, preV1] = v1.GOLDEN.filter((g) => g.message === 'hello' && g.direction === 'reply');
  const hello = v1.parseHello(product!.fields['info'] as string);
  assert.equal(hello.proto, v1.VERSION);
  assert.equal(hello.preV1, false);
  for (const name of Object.keys(v1.COMMANDS) as v1.CommandName[])        // stage2.info: only with stage 2 loaded
    assert.equal(hello.answers(v1.TYPE[name]), v1.COMMANDS[name].switch !== 'XYF_STAGE2_LOADED');
  assert.equal(hello.answers(0x06), false);
  const old = v1.parseHello(preV1!.fields['info'] as string);
  assert.equal(old.preV1, true);
  assert.equal(old.answers(0x06), true);
});

check('optional fields are all or none', () => {
  const path = '/yaffs2/firmware.version';
  assert.deepEqual(v1.decodeFileReadRequest(v1.encodeFileReadRequest({ path })), { path });
  const partial = { path, offset: 0 } as unknown as v1.FileReadRequest;
  assert.throws(() => v1.encodeFileReadRequest(partial), TypeError);
  const offsetOnly = v1.fromHex(v1.toHex(v1.encodeFileReadRequest({ path })) + '00000000');
  assert.throws(() => v1.decodeFileReadRequest(offsetOnly), RangeError);
});

check('text encodings', () => {
  const path = '/yaffs2/xy-forever/café.bin';
  const payload = v1.encodeFileReadRequest({ path, offset: 0, length: 16 });
  assert.equal(v1.toHex(payload.subarray(19, 24)), '636166c3a9');
  assert.deepEqual(v1.decodeFileReadRequest(payload), { path, offset: 0, length: 16 });
  assert.throws(() => v1.encodeLibrarySetDefaultRequest({ id: 'café' }), RangeError);
});

check('common statuses come alone', () => {
  const reply = v1.decodeFileWriteStagedReply(Uint8Array.of(v1.STATUS.state, 0, 0, 0));
  assert.deepEqual(reply, { ok: false, status: v1.STATUS.state, fields: null });
  assert.equal(v1.statusName(99), 'status_99');
});

console.log(`ok ${checks} checks`);
