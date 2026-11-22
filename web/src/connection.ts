// The app's state outside React: the XY Forever connection and what the shell shows about it. Nothing here opens or
// closes the port (device.ts does, from a click), and the store survives a Vite hot update, so neither StrictMode's
// double effects nor HMR touch the link.
import type { Client } from 'xy-forever-client';
import { create } from 'zustand';

export interface Connection {
  client: Client | null;
  /** The install handed over: the "XY Forever is installed" notice shows until dismissed. */
  installed: boolean;
  /** An uninstall runs: the app stays on the Device page. */
  uninstalling: boolean;
  /** An uninstall restored stock OS 1.2.1: Connect says so until the next connection. */
  stockRestored: boolean;
}

const store = () =>
  create<Connection>(() => ({ client: null, installed: false, uninstalling: false, stockRestored: false }));
export const useConnection: ReturnType<typeof store> = import.meta.hot?.data.connection ?? store();
if (import.meta.hot) import.meta.hot.data.connection = useConnection;
