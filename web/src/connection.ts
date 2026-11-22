// The app's state outside React: the XY Forever connection and what the shell shows about it.
import type { Client } from 'xy-forever-client';
import { create } from 'zustand';
import { BugReport } from './bug-report.ts';

export interface Connection {
  client: Client | null;
  /** The install handed over: the "XY Forever is installed" notice shows until dismissed. */
  installed: boolean;
}

export const useConnection = create<Connection>(() => ({ client: null, installed: false }));
/** The bug report's activity and last known device, for this tab. */
export const bugReport = new BugReport();
export const sim = new URLSearchParams(location.search).get('sim');
