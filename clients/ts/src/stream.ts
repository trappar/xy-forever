// Streamed, replace-safe file writes (protocol 1.18: file.write_begin, file.write_data, file.write_end; #4). The bytes
// go straight into a temporary file on the OP-XY; the device checks them, reads the file back and renames it onto the
// target in one step, so a cut cable or a power cut leaves the old file or the new one. No staging-size limit.
import { XyfError, statusError } from './errors.ts';
import { RESEND_MS, sha256 } from './client.ts';
import type { Client, TransferOptions } from './client.ts';
import * as v1 from './v1.ts';

/** Client.writeFile on a 1.18 device: the caller checked the path (checkChange) first. */
export async function writeStream(
  client: Client,
  path: string,
  data: Uint8Array,
  options: TransferOptions = {},
): Promise<{ written: number; readbackCrc: number }> {
  const crc = v1.crc32(data);
  const call = { signal: options.signal, timeoutMs: options.timeoutMs };
  await client.call('file.write_begin', { path, size: data.length, crc, sha256: await sha256(data) }, call);
  // 1.33 (hello pipe): MAX_DATA pieces with pipe of them in flight; before, PUT_CHUNK pieces one at a time.
  const depth = client.info.hello.pipe;
  const step = depth > 1 ? v1.MAX_DATA : v1.PUT_CHUNK;
  const command = v1.COMMANDS['file.write_data'];
  const codec = v1.codecs['file.write_data'];
  let sent = 0,
    answered = 0;
  // A resend's reply (a lost one's, pipelined) says what the device has by then: at least this piece's end.
  const check = (received: number, at: number, resend = false): void => {
    const end = Math.min(at + step, data.length);
    if (resend ? received < end || received > data.length : received !== end) {
      throw new XyfError('protocol', `file.write_data: the device has ${received} bytes, expected ${end}`, {
        command: 'file.write_data',
      });
    }
    options.onProgress?.(end, data.length);
  };
  if (depth > 1 && data.length > 0) {
    await client.link.pipeline(command.type, {
      depth,
      timeoutMs: options.timeoutMs ?? command.timeoutMs,
      resendMs: RESEND_MS,
      signal: options.signal,
      next: () => {
        if (sent >= data.length) return null;
        const payload = codec.encodeRequest({ offset: sent, data: data.subarray(sent, sent + step) });
        sent += step;
        return payload;
      },
      reply: (payload) => {
        const decoded = codec.decodeReply(payload);
        if (!decoded.ok) throw statusError(command.name, decoded.status, decoded.fields);
        check(decoded.fields.received, answered, true);
        answered += step;
        return true;
      },
    });
  } else {
    for (let at = 0; at < data.length; at += step) {
      const { received } = await client.call(
        'file.write_data',
        { offset: at, data: data.subarray(at, at + step) },
        call,
      );
      check(received, at);
    }
  }
  const reply = await client.call('file.write_end', undefined, {
    signal: options.signal,
    timeoutMs: options.timeoutMs ?? 60_000 + Math.ceil(data.length / 20),
  });
  if (reply.written !== data.length || reply.readback_crc !== crc) {
    throw new XyfError(
      'crc',
      `file.write_end ${path}: ${reply.written} of ${data.length} bytes written, read-back CRC ` +
        `${reply.readback_crc.toString(16)}, expected ${crc.toString(16)}`,
      { command: 'file.write_end' },
    );
  }
  return { written: reply.written, readbackCrc: reply.readback_crc };
}
