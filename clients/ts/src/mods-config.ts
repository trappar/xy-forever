import type { CallOptions, Client } from './client.ts';
import { isXyfError } from './errors.ts';
import { DOCUMENT_BYTES, nativeConfigText } from './native-json.ts';

const root = '/yaffs2/xy-forever';
const names = ['config.json', 'config.json.old', 'config.txt', 'config.old'] as const;

/** The selected saved record as a bounded key/value view. A missing record returns null. */
export async function readModsConfig(client: Client, options: CallOptions = {}): Promise<string | null> {
  let entries;
  try {
    entries = await client.listDir(root, options);
  } catch (error) {
    if (isXyfError(error, 'not_found')) return null;
    throw error;
  }
  const entry = names.find((name) => entries.some((item) => item.name === name));
  if (entry === undefined) return null;
  const native = entry === 'config.json' || entry === 'config.json.old';
  const limit = native ? DOCUMENT_BYTES : 2048;
  const buffer = new Uint8Array(limit + 1);
  let offset = 0;
  try {
    while (offset < buffer.length) {
      const length = Math.min(client.info.limits.read, buffer.length - offset);
      const bytes = await client.readRange(`${root}/${entry}`, offset, length, options);
      buffer.set(bytes, offset);
      offset += bytes.length;
      if (bytes.length < length) break;
    }
    if (offset > limit) throw new Error(`exceeds ${limit} bytes`);
    const data = buffer.subarray(0, offset);
    const text = new TextDecoder().decode(native ? nativeConfigText(data) : data);
    if (
      !native &&
      text.split('\n').some((line) => line.startsWith('format=') && line.replace(/\r$/, '') !== 'format=1')
    )
      throw new Error('unsupported legacy configuration format');
    return text;
  } catch (error) {
    throw new Error(`${entry}: ${error instanceof Error ? error.message : String(error)}`, { cause: error });
  }
}
