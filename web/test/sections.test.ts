// The site's two sections (#316): it opens on Install and switches to Manage by itself when XY Forever answers on a
// port this browser was given (on load, at a stage, or when a port appears), and stays where it is when nothing
// answers. The install flow and the app's finder (detect.ts) run as they do in the page; the router's navigation and
// the port's opening are stand-ins.
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { XyfError } from 'xy-forever-client';
import type { Client } from 'xy-forever-client';
import { STOCK_121 } from '../src/install/checks.ts';
import { ENTRY } from '../src/install/finish.ts';
import { installFlow } from '../src/install/flow.ts';

const app = vi.hoisted(() => ({ paths: [] as string[], answering: null as unknown, opens: 0 }));
vi.mock('../src/routing.ts', () => ({
  navigatePath: (path: string) => app.paths.push(path),
  backDepth: () => 0,
  simPort: () => '7341', // a port to look on (the simulator's stand-in)
}));
vi.mock('../src/device.ts', async (actual) => ({
  ...(await actual<typeof import('../src/device.ts')>()),
  openDevice: async () => {
    app.opens++;
    if (!app.answering) throw new XyfError('disconnected', 'no granted XY Forever port answered');
    return app.answering;
  },
}));
const { useConnection } = await import('../src/connection.ts');
const { disconnect } = await import('../src/device.ts');
const { findDevice, toManage, watchForDevice } = await import('../src/detect.ts');

const INFO: Record<string, string> = { type: 'firmware', name: 'OP-XY', version: '1.2.1', sha256: STOCK_121.sha256 };
/** XY Forever as its setup leaves it: no job, the stock entry in the library and the default. */
function installed(): Client {
  const fake = {
    info: { version: '0.1.0', image: 'abcd1234', serial: 'X1' },
    connected: true,
    onAsk: () => {},
    close: async () => {
      fake.connected = false;
    },
    call: async () => {
      throw new XyfError('not_found', 'job.status: not found', { command: 'job.status', status: 3 });
    },
    libraryEntry: async (entry: string) => {
      if (entry !== ENTRY) throw new XyfError('not_found', 'no such file', { command: 'file.read', status: 3 });
      return INFO;
    },
    readState: async () => ({ default: ENTRY }),
  };
  return fake as unknown as Client;
}

/** The Install page's flow with the app's finder and hand-over (pages/install.tsx). */
function install() {
  const flow = installFlow({
    midi: null,
    usb: null,
    serial: true,
    connect: async () => {
      throw new Error('no click in these tests');
    },
    find: async () => {
      const found = await findDevice();
      return found && { client: found, close: () => found.close() };
    },
    handoff: (lease, done) => toManage(lease.client, done),
    release: { schema: 1, version: '0.1.0', image: 'abcd1234', sha256: 'a'.repeat(64), bytes: 1 },
    canary: async () => null,
  });
  flow.setShown(true);
  return flow;
}

beforeEach(() => {
  vi.useFakeTimers();
  app.paths = [];
  app.answering = null;
  app.opens = 0;
  useConnection.setState({ client: null, installed: false, held: false });
});
afterEach(() => {
  vi.useRealTimers();
});

describe('Install', () => {
  it('switches to Manage when XY Forever answers on load', async () => {
    const device = (app.answering = installed());
    install().show('/install/boot', 'load');
    await vi.waitFor(() => expect(app.paths).toEqual(['/manage']));
    expect(useConnection.getState()).toMatchObject({ client: device, installed: false });
  });
  it('stays on Install when nothing answers', async () => {
    const flow = install();
    flow.show('/install/boot', 'load');
    await findDevice(); // after the page's look
    expect(app.opens).toBe(2);
    expect(app.paths).toEqual([]);
    expect(flow.view.getState().stage).toBe('boot');
    expect(useConnection.getState().client).toBeNull();
  });
  it('switches when XY Forever answers after a port appears', async () => {
    const events = new EventTarget();
    const flow = installFlow({ ...install().deps, onPort: (listener) => events.addEventListener('connect', listener) });
    flow.setShown(true);
    flow.show('/install/boot', 'load');
    await findDevice();
    expect(app.paths).toEqual([]);
    app.answering = installed(); // switched on holding PLAY
    events.dispatchEvent(new Event('connect'));
    await vi.advanceTimersByTimeAsync(500);
    await vi.waitFor(() => expect(app.paths).toEqual(['/manage']));
  });
  it('after an install, Manage opens on Mods with the installed notice', async () => {
    app.answering = installed();
    install().show('/install/start', 'push');
    await vi.waitFor(() => expect(app.paths).toEqual(['/manage/mods']));
    expect(useConnection.getState().installed).toBe(true);
  });
});

describe('Manage without a connection', () => {
  it('takes XY Forever when it answers as the page shows', async () => {
    const device = (app.answering = installed());
    const stop = watchForDevice(new EventTarget());
    await findDevice();
    expect(useConnection.getState().client).toBe(device);
    stop();
  });
  it('stays on Connect when nothing answers, then takes XY Forever when a port appears', async () => {
    const events = new EventTarget();
    const stop = watchForDevice(events);
    await findDevice();
    expect(useConnection.getState().client).toBeNull();
    const device = (app.answering = installed());
    events.dispatchEvent(new Event('connect'));
    await vi.advanceTimersByTimeAsync(500);
    await findDevice();
    expect(useConnection.getState().client).toBe(device);
    stop();
  });
  it('never reconnects by itself after the owner disconnects', async () => {
    useConnection.setState({ client: (app.answering = installed()) });
    await disconnect();
    const stop = watchForDevice(new EventTarget());
    await findDevice();
    expect(app.opens).toBe(0);
    expect(useConnection.getState().client).toBeNull();
    stop();
  });
});
