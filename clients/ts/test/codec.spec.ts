// Frame codec: encodeFrame and the resynchronising FrameParser (src/v1.ts, generated) against the
// spec's golden frames and stream vectors, and the cases a serial link produces: noise, split
// reads, back-to-back frames, a bad CRC, an oversized length, a reset after a timeout.
import { describe, expect, test } from 'vitest';
import * as v1 from '../src/v1.ts';

const hex = v1.fromHex;
const frame = (type: number, seq: number, payload: Uint8Array = new Uint8Array(0)): Uint8Array =>
  v1.encodeFrame(type, seq, payload);
const cat = (...parts: Uint8Array[]): Uint8Array => {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) {
    out.set(p, at);
    at += p.length;
  }
  return out;
};
const ramp = (n: number): Uint8Array => Uint8Array.from({ length: n }, (_, i) => (i * 7 + 3) & 0xff);
const types = (frames: v1.Frame[]): [number, number, number][] => frames.map((f) => [f.type, f.seq, f.payload.length]);

describe('frame codec', () => {
  test("CRC-32 is zlib's", () => {
    expect(v1.crc32(new TextEncoder().encode('123456789'))).toBe(0xcbf43926);
    expect(v1.crc32(new TextEncoder().encode('6789'), v1.crc32(new TextEncoder().encode('12345')))).toBe(0xcbf43926);
  });

  test.each(v1.GOLDEN.map((g) => [g.name, g] as const))('golden frame: %s', (_, g) => {
    const [found, ...more] = new v1.FrameParser().feed(hex(g.hex));
    expect(more).toEqual([]);
    expect(v1.toHex(v1.encodeFrame(found!.type, found!.seq, found!.payload))).toBe(g.hex);
  });

  test.each(v1.STREAMS.map((s) => [s.name, s] as const))('stream vector: %s', (_, s) => {
    const want = s.frames.map((f) => ({ type: f.type, seq: f.seq, payload: hex(f.payload) }));
    const chunked = new v1.FrameParser();
    expect(s.chunks.flatMap((c) => chunked.feed(hex(c)))).toEqual(want);
    const whole = hex(s.chunks.join(''));
    expect(new v1.FrameParser().feed(whole)).toEqual(want);
    const bytewise = new v1.FrameParser();
    expect([...whole].flatMap((b) => bytewise.feed(Uint8Array.of(b)))).toEqual(want);
  });

  test('noise before a frame is skipped, magic look-alikes included', () => {
    const parser = new v1.FrameParser();
    const noise = Uint8Array.from([0x00, 0x58, 0x58, 0x46, 0xff, 0x58, 0x46, 0x01, 0x02, 0x58]);
    expect(types(parser.feed(cat(noise, frame(0x81, 7, ramp(5)))))).toEqual([[0x81, 7, 5]]);
    expect(parser.dropped).toBeGreaterThan(0);
  });

  test('a frame split at every byte position, in two reads', () => {
    const f = frame(0x83, 9, ramp(300));
    for (let cut = 0; cut <= f.length; cut++) {
      const parser = new v1.FrameParser();
      const got = [...parser.feed(f.subarray(0, cut)), ...parser.feed(f.subarray(cut))];
      expect(types(got)).toEqual([[0x83, 9, 300]]);
    }
  });

  test('back-to-back frames in one read, and across reads', () => {
    const stream = cat(frame(0x81, 1, ramp(3)), frame(0xd0, 0, ramp(20)), frame(0x83, 2), frame(0x84, 3, ramp(1028)));
    expect(types(new v1.FrameParser().feed(stream))).toEqual([
      [0x81, 1, 3],
      [0xd0, 0, 20],
      [0x83, 2, 0],
      [0x84, 3, 1028],
    ]);
    const parser = new v1.FrameParser();
    const got: v1.Frame[] = [];
    for (let at = 0; at < stream.length; at += 37) got.push(...parser.feed(stream.subarray(at, at + 37)));
    expect(got.length).toBe(4);
  });

  test('a bad CRC drops the frame and the next frame is still found', () => {
    const bad = frame(0x81, 4, ramp(10));
    bad[bad.length - 1]! ^= 0x01;
    const parser = new v1.FrameParser();
    expect(types(parser.feed(cat(bad, frame(0x81, 5, ramp(2)))))).toEqual([[0x81, 5, 2]]);
    expect(parser.badCrc).toBe(1);
  });

  test('an oversized length is noise: the next frame is not swallowed', () => {
    const header = Uint8Array.from([0x58, 0x46, 0x81, 0x06, 0x41, 0x10, 0x00, 0x00]); // length 4161
    const parser = new v1.FrameParser();
    expect(types(parser.feed(cat(header, frame(0x81, 7, ramp(4)))))).toEqual([[0x81, 7, 4]]);
  });

  test('the largest payload round-trips; a larger one is refused', () => {
    const payload = ramp(v1.MAX_PAYLOAD);
    const [f] = new v1.FrameParser().feed(frame(0x91, 200, payload));
    expect(f!.payload).toEqual(payload);
    expect(() => v1.encodeFrame(0x11, 1, ramp(v1.MAX_PAYLOAD + 1))).toThrow(RangeError);
  });

  test('reset() drops a partial frame (after a reply timeout)', () => {
    const parser = new v1.FrameParser();
    const late = frame(0x83, 1, ramp(50));
    expect(parser.feed(late.subarray(0, 20))).toEqual([]);
    parser.reset();
    expect(types(parser.feed(cat(late.subarray(20), frame(0x83, 2, ramp(1)))))).toEqual([[0x83, 2, 1]]);
  });
});
