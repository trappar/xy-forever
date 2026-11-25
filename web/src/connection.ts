// The app's state outside React: the XY Forever connection and what the shell shows about it. Nothing here opens or
// closes the port (device.ts does, from a click), and the store survives a Vite hot update, so neither StrictMode's
// double effects nor HMR touch the link.
import type { Client } from 'xy-forever-client';
import { create } from 'zustand';

interface Connection {
  client: Client | null;
  /** The install handed over: the "XY Forever is installed" notice shows until dismissed. */
  installed: boolean;
  /** With it: what the first start removed of Open-XY's content, in words (#334), or null. */
  cleaned: string | null;
  /** An uninstall runs: the app stays on the Device page. */
  uninstalling: boolean;
  /** An uninstall restored stock OS 1.2.1: the app says so until the next connection. */
  stockRestored: boolean;
  /** The owner disconnected: no look without a click (detect.ts) until the next connection or a reload. */
  held: boolean;
}

const store = () =>
  create<Connection>(() => ({
    client: null,
    installed: false,
    cleaned: null,
    uninstalling: false,
    stockRestored: false,
    held: false,
  }));
export const useConnection: ReturnType<typeof store> = import.meta.hot?.data.connection ?? store();
if (import.meta.hot) import.meta.hot.data.connection = useConnection;
