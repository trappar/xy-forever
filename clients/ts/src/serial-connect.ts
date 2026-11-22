// Connecting over Web Serial without asking the owner to choose (#178): a port Chrome already granted is used
// when it answers HELLO; the chooser opens only when none does, filtered to the given USB ids. Chrome may list a
// port that opens but never answers (an entry left from before a restart, or another device on the same ids: an
// install saw two entries): a probe passes over it, here and when reconnecting after a restart.
import { Client } from './client.ts';
import type { ConnectOptions } from './client.ts';
import { XyfError, isXyfError } from './errors.ts';
import type { Opener, Transport, TransportSink } from './transport.ts';
import { USB_FILTERS } from './v1.ts';
import { WebSerialTransport, grantedPorts, webSerial } from './webserial.ts';
import type { SerialLike, SerialPortLike } from './webserial.ts';

type Filter = { readonly usbVendorId: number; readonly usbProductId: number };

/** The one USB id pair a release image's service has (the composite from 189-002, and its serial fallback). */
export const RELEASE_FILTERS: readonly Filter[] = [USB_FILTERS[1]];

/** What to choose when Chrome lists more than one entry: XY Forever's composite and its stock-CDC fallback service
 * (docs/protocol/transport.md) both speak XY Forever's protocol. */
export const CHOOSE_TEXT =
  'Chrome may list both "OP-XY (XY Forever)" and "OP_XY Boot": both are XY Forever, and either is fine to choose.';

export interface SerialConnectOptions extends ConnectOptions {
  serial?: SerialLike | null | undefined;
  /** The ports considered and the chooser's filter; default USB_FILTERS. */
  filters?: readonly Filter[] | undefined;
  /** A port that does not answer within this after the first request is passed over; default 1500 ms. */
  probeMs?: number | undefined;
  /** false: never open the chooser (no click to answer it); no granted port answering throws `disconnected`. */
  choose?: boolean | undefined;
}

/**
 * Connects to XY Forever: each granted port of filters in turn (no chooser), else the chooser (call from a click:
 * a silent granted port costs probeMs, so the click still counts). The client's reconnect finds the port again the
 * same way, never with the chooser.
 */
export async function connectWebSerial(options: SerialConnectOptions = {}): Promise<Client> {
  const serial = options.serial === undefined ? webSerial() : options.serial;
  if (!serial) throw new Error('this browser has no Web Serial: use Chrome, Edge or Opera on a computer');
  const filters = options.filters ?? USB_FILTERS,
    probeMs = options.probeMs ?? 1500;
  const picker = new PortPicker(serial, filters, probeMs);
  if ((await grantedPorts(serial)).some((port) => matches(port, filters))) {
    try {
      const client = await Client.connect(picker.opener, options);
      picker.probing = false; // found: reconnect tries every granted port again, as after the chooser
      return client;
    } catch (error) {
      if (error !== picker.none) throw error;
    }
  }
  if (options.choose === false) throw new XyfError('disconnected', 'no granted XY Forever port answered');
  picker.probing = false;
  picker.preferred = await serial.requestPort({ filters: filters.map((f) => ({ ...f })) });
  picker.silent.delete(picker.preferred);
  try {
    return await Client.connect(picker.opener, options);
  } catch (error) {
    if (!isXyfError(error, 'timeout')) throw error;
    throw new XyfError(
      'timeout',
      `The chosen port did not answer as XY Forever. Is your OP-XY in the XY Forever menu? ${CHOOSE_TEXT}`,
      { cause: error },
    );
  }
}

function matches(port: SerialPortLike, filters: readonly Filter[]): boolean {
  const info = port.getInfo();
  return filters.some((f) => info.usbVendorId === f.usbVendorId && info.usbProductId === f.usbProductId);
}

/** Chrome's answer to opening a port this page has open already (another link here holds it). */
function openHere(error: unknown): boolean {
  return (error as { name?: unknown } | null)?.name === 'InvalidStateError';
}

/**
 * Opens the preferred port, else the first granted one not found silent. While probing (before the chooser), a port
 * that fails to open is silent too, and none left ends the connect (`none`); after it, all are tried again. A port
 * this page has open already ends the connect (`held`): another link here owns XY Forever, and the next entry may be
 * the same OP-XY under another name (Chrome listed two for one), so opening it would put a second reader on its replies.
 */
class PortPicker {
  readonly silent = new Set<SerialPortLike>();
  readonly none = new XyfError('aborted', 'no granted port answered'); // handshake gives an opener's `aborted` back at once
  readonly held = new XyfError('aborted', 'XY Forever is already connected in this page: close that connection first');
  preferred: SerialPortLike | null = null;
  probing = true;
  private readonly serial: SerialLike;
  private readonly filters: readonly Filter[];
  private readonly probeMs: number;
  constructor(serial: SerialLike, filters: readonly Filter[], probeMs: number) {
    this.serial = serial;
    this.filters = filters;
    this.probeMs = probeMs;
  }

  readonly opener: Opener = async () => {
    for (;;) {
      const ports = (await grantedPorts(this.serial)).filter((port) => matches(port, this.filters));
      for (const port of this.silent) if (!ports.includes(port)) this.silent.delete(port);
      let candidates = ports.filter((port) => !this.silent.has(port));
      if (candidates.length === 0) {
        if (this.probing) throw this.none;
        if (ports.length === 0) throw new Error('no XY Forever port granted or attached');
        this.silent.clear();
        candidates = ports;
      }
      const port = this.preferred && candidates.includes(this.preferred) ? this.preferred : candidates[0]!;
      const quiet = (): void => {
        this.silent.add(port);
        if (port === this.preferred) this.preferred = null;
      };
      try {
        return new Probe(await WebSerialTransport.open(port), this.probeMs, quiet);
      } catch (error) {
        if (openHere(error)) throw this.held;
        if (!this.probing) throw error;
        quiet();
      }
    }
  };
}

/** Closes the link when nothing comes back within ms of the first write (then silent runs), so the next port is tried. */
class Probe implements Transport {
  private timer: ReturnType<typeof setTimeout> | null = null;
  private heard = false;
  private readonly inner: Transport;
  private readonly ms: number;
  private readonly silent: () => void;
  constructor(inner: Transport, ms: number, silent: () => void) {
    this.inner = inner;
    this.ms = ms;
    this.silent = silent;
  }

  start(sink: TransportSink): void {
    this.inner.start({
      data: (bytes) => {
        this.heard = true;
        if (this.timer) clearTimeout(this.timer);
        this.timer = null;
        sink.data(bytes);
      },
      closed: (error) => {
        if (this.timer) clearTimeout(this.timer);
        this.timer = null;
        sink.closed(error);
      },
    });
  }

  write(data: Uint8Array): Promise<void> {
    if (!this.heard && !this.timer)
      this.timer = setTimeout(() => {
        this.silent();
        void this.inner.close();
      }, this.ms);
    return this.inner.write(data);
  }

  close(): Promise<void> {
    return this.inner.close();
  }
}
