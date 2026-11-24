// Finding XY Forever without a click (#316). The site opens on Install; when an OP-XY this browser was given before (a
// granted Web Serial port: never the chooser) or the simulator answers as XY Forever, the app takes the connection
// and switches to Manage. Install looks through its flow (install/flow.ts: on load, at each stage, when a port
// appears), which also follows XY Forever's first setup, then calls toManage; Manage without a connection looks when
// it shows and when a port appears (watchForDevice). One look at a time, so two never open two links on one OP-XY;
// none while a device call runs, or after the owner disconnected (until the next connection or a reload).
import type { Client, SerialConnectOptions } from 'xy-forever-client';
import { bugReport } from './bug-report.ts';
import { useConnection } from './connection.ts';
import { adopt, canConnect, deviceBusy, openDevice } from './device.ts';
import { navigatePath } from './routing.ts';

/** After a port appears, the looks: it may answer only after a moment. */
const AFTER_PORT_MS = [500, 2000, 5000];

let last: Promise<unknown> = Promise.resolve();

/** Makes what answered the app's connection: a connect, as the Connect click's. */
function take(found: Client): void {
  adopt(found);
  bugReport.record('connect', 'finished');
}

/** XY Forever on a granted port (or the simulator), else null: none answers, the app holds a connection, a device call
 * runs, or the owner disconnected. */
export function findDevice(filters?: SerialConnectOptions['filters']): Promise<Client | null> {
  const look = last.then(async () => {
    const { client, held } = useConnection.getState();
    if (client || held || deviceBusy() || !canConnect()) return null;
    try {
      return await openDevice({ timeoutMs: 5_000, choose: false, filters });
    } catch {
      return null; // nothing answered
    }
  });
  last = look;
  return look;
}

/** XY Forever answered while Install showed: the app takes the connection, then Manage; right after an install
 * (installed), its Mods page with the "installed" notice. */
export function toManage(found: Client, installed: boolean): void {
  if (useConnection.getState().client !== found) take(found);
  if (installed) useConnection.setState({ installed: true });
  navigatePath(installed ? '/manage/mods' : '/manage');
}

/** Manage without a connection: looks now and whenever a port appears, and takes XY Forever when it answers. Returns
 * the stop. */
export function watchForDevice(serial: EventTarget | null | undefined): () => void {
  let live = true;
  const timers: ReturnType<typeof setTimeout>[] = [];
  const look = async (): Promise<void> => {
    if (!live) return;
    const found = await findDevice();
    if (!found) return;
    if (live && !useConnection.getState().client) take(found);
    else await found.close().catch(() => {});
  };
  const appeared = (): void => {
    for (const ms of AFTER_PORT_MS) timers.push(setTimeout(() => void look(), ms));
  };
  void look();
  serial?.addEventListener('connect', appeared);
  return () => {
    live = false;
    serial?.removeEventListener('connect', appeared);
    timers.forEach(clearTimeout);
  };
}
