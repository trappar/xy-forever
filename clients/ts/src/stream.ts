// Streamed, replace-safe file writes (protocol 1.18: file.write_begin, file.write_data, file.write_end; #4). The bytes
// go straight into a temporary file on the OP-XY; the device checks them, reads the file back and renames it onto the
// target in one step, so a cut cable or a power cut leaves the old file or the new one. No staging-size limit.
import { XyfError } from './errors.ts';
import { sha256 } from './client.ts';
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
  for (let at = 0; at < data.length; at += v1.PUT_CHUNK) {
    const piece = data.subarray(at, at + v1.PUT_CHUNK);
    const { received } = await client.call('file.write_data', { offset: at, data: piece }, call);
    if (received !== at + piece.length) {
      throw new XyfError(
        'protocol',
        `file.write_data: the device has ${received} bytes, expected ${at + piece.length}`,
        { command: 'file.write_data' },
      );
    }
    options.onProgress?.(at + piece.length, data.length);
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
