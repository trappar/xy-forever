// The Install page, not moved to React yet: plain DOM in one section kept across its stages.
import { RELEASE_FILTERS } from 'xy-forever-client';
import type { Client } from 'xy-forever-client';
import { useConnection } from './connection.ts';
import { adopt, canConnect, deviceBusy, openDevice } from './device.ts';
import { installPage } from './install/page.ts';
import type { Lease } from './install/page.ts';
import { loadPath, navigatePath as navigate } from './routing.ts';

let shownInstall = false;
/** The install flow's one section, kept across its stages. */
export function installSection(): HTMLElement {
  install ??= installPage(installRoot, installDeps());
  return installRoot;
}
// Uninstalled: the install flow starts over.
useConnection.subscribe((now, before) => { if (now.stockRestored && !before.stockRestored) install?.reset(); });
const installRoot = Object.assign(document.createElement('section'), { id: 'page-install', className: 'page', hidden: true });
/** Shows an install stage: the first page this document shows counts as the load (a reload asks te-boot again). */
export function showInstall(stage: string | undefined): void {
  const how = shownInstall || !loadPath.includes('/install/') ? 'push' : 'load';
  shownInstall = true;
  installSection();
  install!.show(`/install/${stage ?? ''}`, how);
}


// ---- Install (#178) -----------------------------------------------------------------------------------

let install: ReturnType<typeof installPage> | null = null;

function installDeps(): Parameters<typeof installPage>[1] {
  const nav = navigator as Navigator & { requestMIDIAccess?: (o: { sysex: boolean }) => Promise<unknown>; usb?: unknown;
    serial?: { addEventListener?: (type: string, listener: () => void) => void } };
  return {
    midi: nav.requestMIDIAccess ? nav.requestMIDIAccess.bind(nav) : null,
    usb: nav.usb ?? null,
    serial: canConnect(),
    connect: async () => own() ?? lease(await openDevice({ filters: RELEASE_FILTERS })),
    find: findDevice,
    handoff: (lease) => {                     // installed: the connection becomes the app's, then the Mods page
      if (useConnection.getState().client !== lease.client) adopt(lease.client);
      useConnection.setState({ installed: true });
      navigate('/mods');
    },
    onPort: (listener) => nav.serial?.addEventListener?.('connect', listener),
  };
}

/** This app's connection, lent (close keeps it): while it holds the port, the Install page uses it and never opens a
 * second link (two readers on one OP-XY split its replies). */
function own(): Lease | null { const { client } = useConnection.getState(); return client?.connected ? { client, close: async () => {} } : null; }
const lease = (found: Client): Lease => ({ client: found, close: () => found.close() });

/** XY Forever without the chooser, for the Install page to detect: this app's connection, else a granted port that
 * answers (or the simulator); null when none does. */
async function findDevice(): Promise<Lease | null> {
  const mine = own();
  if (mine) return mine;
  if (deviceBusy() || !canConnect()) return null;
  return lease(await openDevice({ timeoutMs: 5_000, filters: RELEASE_FILTERS, choose: false }));
}
