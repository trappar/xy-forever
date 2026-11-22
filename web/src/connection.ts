// The app's state outside React: the XY Forever connection and what the shell shows about it.
import type { Client } from 'xy-forever-client';
import { create } from 'zustand';

export interface Connection {
  client: Client | null;
  /** The install handed over: the "XY Forever is installed" notice shows until dismissed. */
  installed: boolean;
  /** An uninstall runs: the app stays on the Device page. */
  uninstalling: boolean;
}

export const useConnection = create<Connection>(() => ({ client: null, installed: false, uninstalling: false }));
