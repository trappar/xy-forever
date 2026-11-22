// Device calls: the connection, opened only from a click (Connect, the Install page's Find), and TanStack Query over it.
// Every mutation shares one scope, so the OP-XY gets one action at a time (a second waits for the first); queries read
// what the pages show. Each outcome goes to the bug report, a failure to the notice line, and a lost link to Connect.
import { MutationCache, MutationObserver, QueryCache, QueryClient, useMutation } from '@tanstack/react-query';
import {
  ASK_TEXT,
  Client,
  JobError,
  SERIAL_OFF_TEXT,
  connectWebSerial,
  fileReason,
  isXyfError,
  readModsConfig,
  webSerial,
} from 'xy-forever-client';
import type { SerialConnectOptions } from 'xy-forever-client';
import { webSocketOpener } from 'xy-forever-client/dev';
import { bugReport } from './bug-report.ts';
import type { Action } from './bug-report.ts';
import { useConnection } from './connection.ts';
import { parseConfig } from './model.ts';
import type { SavedConfig } from './model.ts';
import { notice } from './notice.ts';
import { navigatePath, simPort } from './routing.ts';

type DeviceMeta = { action: Action; label?: string };
declare module '@tanstack/react-query' {
  interface Register {
    queryMeta: DeviceMeta;
    mutationMeta: DeviceMeta;
  }
}

export function describe(error: unknown): string {
  if (error instanceof JobError) return `The build stopped: ${error.message}`;
  if (isXyfError(error, 'busy')) return 'The OP-XY is busy with a build. Wait for it to finish, then try again.';
  if (isXyfError(error)) return error.fields && 'error' in error.fields ? fileReason(error) : error.message;
  return error instanceof Error ? error.message : String(error);
}

function failed(action: Action | undefined, error: unknown): void {
  if (action) bugReport.record(action, 'failed', error);
  const { client } = useConnection.getState();
  if (client && !client.connected) {
    useConnection.setState({ client: null });
    navigatePath('/connect');
  }
  notice(describe(error), 'error');
}

export const queryClient = new QueryClient({
  queryCache: new QueryCache({
    onSuccess: (_data, query) => {
      if (query.meta) bugReport.record(query.meta.action, 'finished');
    },
    onError: (error, query) => failed(query.meta?.action, error),
  }),
  mutationCache: new MutationCache({
    onMutate: (_variables, mutation) => notice(mutation.meta?.label ?? '', 'busy'),
    onSuccess: (_data, _variables, _result, mutation) => {
      if (mutation.meta) bugReport.record(mutation.meta.action, 'finished');
      if (mutation.meta?.label) notice('');
    },
    onError: (error, _variables, _result, mutation) => failed(mutation.meta?.action, error),
  }),
  defaultOptions: {
    queries: { retry: false, staleTime: Infinity, refetchOnWindowFocus: false, refetchOnReconnect: false },
    mutations: { retry: false, scope: { id: 'device' } },
  },
});
// A new connection (or none) makes every answer read so far stale.
useConnection.subscribe((now, before) => {
  if (now.client !== before.client) queryClient.removeQueries();
});

const meta = (action: Action, label: string | undefined): DeviceMeta => (label ? { action, label } : { action });

/** A device action for React: a mutation in the device scope, its outcome reported. */
export function useAction<V = void, T = unknown>(action: Action, work: (variables: V) => Promise<T>, label?: string) {
  return useMutation({ mutationFn: work, meta: meta(action, label) });
}

/** The same outside React; resolves once it ends (a failure is already reported). */
export function runAction(action: Action, work: () => Promise<unknown>, label?: string): Promise<void> {
  return new MutationObserver(queryClient, { mutationFn: work, meta: meta(action, label) }).mutate().then(
    () => {},
    () => {},
  );
}

/** A device action or query runs. */
export const deviceBusy = (): boolean => queryClient.isMutating() + queryClient.isFetching() > 0;

/** The connection, for a page that needs one (the router sends the others to Connect). */
export function device(): Client {
  const { client } = useConnection.getState();
  if (!client) throw new Error('Not connected: connect your OP-XY first.');
  return client;
}

/** Web Serial or the simulator can be used here. */
export const canConnect = (): boolean => simPort() !== null || webSerial() !== null;

/** The simulator, else Web Serial: a granted port that answers, the chooser (only from a click) when none does
 * (serial-connect.ts). filters: the install's release image has only the stock pair (RELEASE_FILTERS). */
export async function openDevice(
  options: Pick<SerialConnectOptions, 'filters' | 'choose' | 'timeoutMs'> = {},
): Promise<Client> {
  const port = simPort();
  if (port)
    return Client.connect(
      webSocketOpener(
        `${location.protocol === 'https:' ? 'wss' : 'ws'}://${location.host}/xyf-relay?sim=${encodeURIComponent(port)}`,
      ),
      { timeoutMs: 10_000 },
    );
  return connectWebSerial({ timeoutMs: 10_000, ...options });
}

/** Makes found this app's connection. */
export function adopt(found: Client): void {
  bugReport.updateStatus(found.info);
  found.onAsk((asking) => notice(asking ? `${ASK_TEXT}: it asks to allow this computer (PLAY allows).` : '', 'busy')); // #65
  useConnection.setState({ client: found, stockRestored: false });
  if (!found.info.serial) notice(SERIAL_OFF_TEXT, 'error'); // #191: hello serial=off
}

/** Closes this app's connection. */
export async function disconnect(): Promise<void> {
  const { client } = useConnection.getState();
  useConnection.setState({ client: null });
  await client?.close();
}

/** A text file on the device, or null when it is not there. */
export async function readText(client: Client, path: string): Promise<string | null> {
  try {
    return new TextDecoder().decode(await client.readFile(path));
  } catch (e) {
    if (isXyfError(e, 'not_found')) return null;
    throw e;
  }
}

/** The saved mods (/yaffs2/xy-forever/config.json): null when none is saved, undefined with the reason when unreadable. */
export async function savedConfig(
  client: Client,
): Promise<{ config: SavedConfig | null; error?: never } | { config: undefined; error: string }> {
  try {
    const text = await readModsConfig(client);
    return { config: text === null ? null : parseConfig(text) };
  } catch (error) {
    return { config: undefined, error: error instanceof Error ? error.message : String(error) };
  }
}
