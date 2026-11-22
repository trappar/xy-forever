// Developer entry: unstable, outside the protocol's compatibility rules, not for an app's UI.
// The raw link (any request type or bytes: conformance tests), the developer commands, and the
// WebSocket transport (the simulator through the development relay, test/relay.ts).
import { invoke } from './client.ts';
import type { AnyCodec, CallOptions, Client } from './client.ts';
import { COMMANDS, dev } from './v1.ts';

export { Link } from './link.ts';
export type { RequestOptions, Trace } from './link.ts';
export { invoke } from './client.ts';
export { WebSocketTransport, webSocketOpener } from './websocket.ts';
export type { WebSocketOptions } from './websocket.ts';

/** diag.status: the device's counters as tagged binary blocks (decoder: xy_forever/census.py). */
export async function diagStatus(client: Client, options: CallOptions = {}): Promise<Uint8Array> {
  const fields = await invoke(
    client,
    COMMANDS['diag.status'],
    dev['diag.status'] as unknown as AnyCodec,
    undefined,
    options,
  );
  return fields['blocks'] as Uint8Array;
}
