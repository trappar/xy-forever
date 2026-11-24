// Where the app reads Open-XY's published kit, fresh on every load, so a newer kit's catalog shows without a new
// deployment of this app. Open-XY's site lets the production origin read it (#185); in development the dev server
// proxies it (vite.config.ts). Serving a same-origin copy instead is a change of KIT_URL alone.
const KIT_URL = import.meta.env.DEV ? '/kit.json.gz' : 'https://open-xy.com/kit.json.gz';

/** The kit could not be downloaded: the message says what to do. */
class KitUnavailable extends Error {
  constructor(reason: string) {
    super(
      `Open-XY's catalog could not be downloaded (${reason}). Check the internet connection and try again; ` +
        'packages from a folder can still be added below.',
    );
    this.name = 'KitUnavailable';
  }
}

/** The published kit as JSON (served gzip-compressed, as application/gzip, or already decoded). */
export async function fetchKit(url = KIT_URL): Promise<unknown> {
  let response: Response;
  try {
    response = await fetch(url, { cache: 'no-cache' });
  } catch {
    throw new KitUnavailable('no answer from open-xy.com');
  }
  if (!response.ok) throw new KitUnavailable(`open-xy.com answered ${response.status}`);
  let bytes = new Uint8Array(await response.arrayBuffer());
  try {
    if (bytes[0] === 0x1f && bytes[1] === 0x8b)
      bytes = new Uint8Array(
        await new Response(new Blob([bytes]).stream().pipeThrough(new DecompressionStream('gzip'))).arrayBuffer(),
      );
    return JSON.parse(new TextDecoder('utf-8', { fatal: true }).decode(bytes));
  } catch {
    throw new KitUnavailable('the download is damaged');
  }
}
