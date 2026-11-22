// Manifest selection and bounded in-memory views. Hash the original bytes before
// semantic admission; a present JSON record must never fall back to legacy text.
import { nativePackageText } from './native-json.ts';
import { isXyfError } from './errors.ts';
import type { CallOptions, Client } from './client.ts';

export const NATIVE_MANIFEST = 'manifest.json',
  LEGACY_MANIFEST = 'info.txt';
export function recordName(names: Iterable<string>): string | undefined {
  const found = new Set(names);
  return found.has(NATIVE_MANIFEST) ? NATIVE_MANIFEST : found.has(LEGACY_MANIFEST) ? LEGACY_MANIFEST : undefined;
}
export function packageText(data: Uint8Array, name = LEGACY_MANIFEST): Uint8Array {
  return name === NATIVE_MANIFEST ? nativePackageText(data) : data;
}
export function packageValues(data: Uint8Array, name = LEGACY_MANIFEST): Record<string, string> {
  const out: Record<string, string> = {};
  for (const line of new TextDecoder().decode(packageText(data, name)).split(/\r\n|\r|\n/)) {
    const at = line.indexOf('=');
    if (at >= 0 && !line.startsWith('#')) out[line.slice(0, at)] = line.slice(at + 1);
  }
  return out;
}

export function keyValues(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const raw of text.split('\n')) {
    const line = raw.trim();
    const at = line.indexOf('=');
    if (line && !line.startsWith('#') && at > 0) out[line.slice(0, at).trim()] = line.slice(at + 1).trim();
  }
  return out;
}

export async function readLibraryEntry(
  client: Client,
  id: string,
  options: CallOptions,
): Promise<Record<string, string>> {
  const root = `/yaffs2/xy-forever/firmware/${id}`;
  let data: Uint8Array;
  try {
    data = await client.readFile(`${root}/${NATIVE_MANIFEST}`, options);
  } catch (error) {
    if (!isXyfError(error, 'not_found')) throw error;
    return keyValues(new TextDecoder().decode(await client.readFile(`${root}/${LEGACY_MANIFEST}`, options)));
  }
  // Keep parse errors outside the absence-only fallback.
  return keyValues(new TextDecoder().decode(packageText(data, NATIVE_MANIFEST)));
}
