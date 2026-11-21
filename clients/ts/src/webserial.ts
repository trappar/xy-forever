// Web Serial transport (Chrome, Edge, Opera on the desktop). Runs in Chrome against
// the simulator through a fake navigator.serial (test/browser/); not yet against an OP-XY.
// Typed with the few parts of the Web Serial API it uses, so it needs no extra type package.
import type { Opener, Transport, TransportSink } from './transport.ts';
import { USB_FILTERS } from './v1.ts';

export interface SerialPortLike {
  open(options: { baudRate: number; bufferSize?: number }): Promise<void>;
  close(): Promise<void>;
  readonly readable: ReadableStream<Uint8Array> | null;
  readonly writable: WritableStream<Uint8Array> | null;
  getInfo(): { usbVendorId?: number; usbProductId?: number };
}
export interface SerialLike {
  requestPort(options?: { filters?: { usbVendorId?: number; usbProductId?: number }[] }): Promise<SerialPortLike>;
  getPorts(): Promise<SerialPortLike[]>;
}

/** navigator.serial, or null where the browser has no Web Serial (tell the user to use the menu or MTP). */
export function webSerial(): SerialLike | null {
  const nav = (globalThis as { navigator?: { serial?: SerialLike } }).navigator;
  return nav?.serial ?? null;
}

function isXyForever(port: SerialPortLike): boolean {
  const info = port.getInfo();
  return USB_FILTERS.some((f) => info.usbVendorId === f.usbVendorId && info.usbProductId === f.usbProductId);
}

/** Chrome's port chooser, showing only XY Forever's service; needs a user gesture (a click). */
export async function requestPort(serial: SerialLike | null = webSerial()): Promise<SerialPortLike> {
  if (!serial) throw new Error('this browser has no Web Serial: use Chrome, Edge or Opera on a computer');
  return serial.requestPort({ filters: USB_FILTERS.map((f) => ({ ...f })) });
}

/** Ports this origin was already granted that are XY Forever's service (no chooser). */
export async function grantedPorts(serial: SerialLike | null = webSerial()): Promise<SerialPortLike[]> {
  return serial ? (await serial.getPorts()).filter(isXyForever) : [];
}

/**
 * How every port is opened. The device ignores the line settings (docs/protocol/transport.md). bufferSize: Chrome's
 * default is 255 bytes, and what arrives while that buffer is full is lost, so a 1 KiB file.read reply (or a hello)
 * would never arrive whole; 64 KiB holds the largest frame many times over.
 */
export const SERIAL_OPEN = { baudRate: 115200, bufferSize: 65536 } as const;

/** XY Forever ports this page holds open (WebSerialTransport.open until release). */
const openHere = new Set<SerialPortLike>();

export class WebSerialTransport implements Transport {
  private readonly port: SerialPortLike;
  private reader: ReadableStreamDefaultReader<Uint8Array> | null = null;
  private done: Promise<void> = Promise.resolve();
  private sink: TransportSink | null = null;
  private ended = false;

  /**
   * Opens the port the one way every path uses (SERIAL_OPEN), then reads it continuously from start(). Refused with
   * InvalidStateError while another XY Forever port is open in this page: Chrome on a Mac can list one OP-XY twice
   * (the cu. and tty. names of its one serial interface), and two open entries would split its replies between two
   * readers.
   */
  static async open(port: SerialPortLike): Promise<WebSerialTransport> {
    const other = [...openHere].find((p) => p !== port && isXyForever(p) && isXyForever(port));
    if (other) throw new DOMException('XY Forever is already open in this page under another entry', 'InvalidStateError');
    await port.open({ ...SERIAL_OPEN });
    openHere.add(port);
    return new WebSerialTransport(port);
  }

  private constructor(port: SerialPortLike) {
    this.port = port;
  }

  start(sink: TransportSink): void {
    this.sink = sink;
    const readable = this.port.readable;
    if (!readable) { this.done = this.release(new Error('the port has no readable stream')); return; }
    const reader = readable.getReader();
    this.reader = reader;
    this.done = (async () => {
      let error: Error | undefined;
      try {
        for (;;) {
          const { value, done } = await reader.read();
          if (done) break;
          if (value) sink.data(value);
        }
      } catch (e) {
        error = e as Error;                             // NetworkError: the device went away (restart, cable)
      } finally {
        reader.releaseLock();
      }
      await this.release(error);
    })();
  }

  async write(data: Uint8Array): Promise<void> {
    const writable = this.port.writable;
    if (this.ended || !writable) throw new Error('the port is closed');
    const writer = writable.getWriter();
    try {
      await writer.write(data);
    } finally {
      writer.releaseLock();
    }
  }

  async close(): Promise<void> {
    if (this.reader) await this.reader.cancel().catch(() => undefined);
    else if (!this.ended) this.done = this.release();
    await this.done;
  }

  /**
   * Closes the port, also after the device went away: Chrome keeps a lost port open until close(),
   * and the same object could not be opened again. Then tells the sink.
   */
  private async release(error?: Error): Promise<void> {
    await this.port.close().catch(() => undefined);
    openHere.delete(this.port);
    if (this.ended) return;
    this.ended = true;
    this.sink?.closed(error);
  }
}

/**
 * Opens the first granted XY Forever port each time it is called (after a restart the port comes
 * back as a new port object, without the chooser only if the device has a USB serial number:
 * unverified). Call requestPort() from a click first.
 */
export function webSerialOpener(): Opener {
  return async () => {
    const [port] = await grantedPorts();
    if (!port) throw new Error('no XY Forever port granted or attached');
    return WebSerialTransport.open(port);
  };
}
