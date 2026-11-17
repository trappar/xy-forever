// A fake navigator.serial for the browser tests: XY Forever's port is the device simulator behind
// the development relay (test/relay.ts); next to it, an unrelated USB serial adapter this origin
// was already granted. It follows Chrome's Web Serial where src/webserial.ts depends on it:
// - requestPort() needs a user gesture (navigator.userActivation), checks the filters as Chrome
//   does, and stands for the chooser: it picks the first matching port, as if the user clicked it;
//   NotFoundError when nothing matches.
// - getPorts() lists the granted ports that are attached. Attached = the simulator's USB endpoint
//   listens (the relay's GET /status). A device that comes back after a restart or a pulled cable is
//   a new SerialPort object, and its grant survives (as with a USB serial number; unverified on a device).
// - open() needs a baud rate, refuses a second open, and fails with NetworkError while the device
//   is away. readable/writable are created on access while open; a byte stream for reading.
// - The relay closing the WebSocket is the device going away: both streams error with
//   NetworkError, then become null, and 'disconnect' fires at the port and at navigator.serial;
//   the port stays open until close(). close() refuses while a stream is locked (TypeError).
// Not modelled: setSignals/getSignals (DTR), the 'connect' event's timing, read errors that leave
// the port usable (break, framing, parity, overrun), exclusive opening against other programs.
import { USB_FILTERS } from '../../src/index.ts';

export interface PortInfo { usbVendorId?: number; usbProductId?: number }
type Filter = { usbVendorId?: number; usbProductId?: number };

const ADAPTER: PortInfo = { usbVendorId: 0x0403, usbProductId: 0x6001 };       // an FTDI USB serial adapter
const lost = (): DOMException => new DOMException('The device has been lost.', 'NetworkError');

function checkFilters(filters: Filter[] | undefined): void {
  for (const f of filters ?? []) {
    if (f.usbVendorId === undefined && f.usbProductId !== undefined) {
      throw new TypeError("Failed to execute 'requestPort' on 'Serial': A filter containing a usbProductId must also specify a usbVendorId.");
    }
    if (f.usbVendorId === undefined) throw new TypeError("Failed to execute 'requestPort' on 'Serial': A filter must provide a property to filter by.");
  }
}

function matches(info: PortInfo, filters: Filter[] | undefined): boolean {
  if (!filters || filters.length === 0) return true;
  return filters.some((f) => f.usbVendorId === info.usbVendorId && (f.usbProductId === undefined || f.usbProductId === info.usbProductId));
}

export class FakePort extends EventTarget {
  /** How many times open() succeeded. */
  opens = 0;
  isLost = false;
  private readonly owner: FakeSerial;
  private readonly info: PortInfo;
  private readonly relay: string | null;
  private socket: WebSocket | null = null;
  private state: 'closed' | 'opening' | 'open' = 'closed';
  private rs: ReadableStream<Uint8Array> | null = null;
  private rc: ReadableByteStreamController | null = null;
  private ws: WritableStream<Uint8Array> | null = null;
  private wc: WritableStreamDefaultController | null = null;
  private held: Uint8Array<ArrayBuffer>[] = [];                    // received while no readable exists

  constructor(owner: FakeSerial, info: PortInfo, relay: string | null) {
    super();
    this.owner = owner;
    this.info = info;
    this.relay = relay;
  }

  getInfo(): PortInfo { return { ...this.info }; }

  /** Opened and not yet closed (a lost port stays open until close(), as in Chrome). */
  get isOpen(): boolean { return this.state !== 'closed'; }

  async open(options: { baudRate: number; bufferSize?: number }): Promise<void> {
    if (this.state !== 'closed') throw new DOMException('The port is already open.', 'InvalidStateError');
    if (!Number.isInteger(options?.baudRate) || options.baudRate <= 0) throw new TypeError('Requested baud rate must be greater than zero.');
    const bufferSize = options.bufferSize ?? 255;
    if (!Number.isInteger(bufferSize) || bufferSize <= 0 || bufferSize > 16 * 1024 * 1024) throw new TypeError('Requested buffer size out of range.');
    if (this.isLost || !this.relay) throw new DOMException('Failed to open serial port.', 'NetworkError');
    this.state = 'opening';
    const socket = new WebSocket(this.relay);
    socket.binaryType = 'arraybuffer';
    const opened = await new Promise<boolean>((resolve) => {
      socket.onopen = () => resolve(true);
      socket.onclose = () => resolve(false);
    });
    if (!opened) { this.state = 'closed'; throw new DOMException('Failed to open serial port.', 'NetworkError'); }
    this.socket = socket;
    this.state = 'open';
    this.opens++;
    socket.onmessage = (e: MessageEvent) => this.received(new Uint8Array(e.data as ArrayBuffer));
    socket.onclose = () => { if (this.socket === socket) this.lose(); };
  }

  get readable(): ReadableStream<Uint8Array> | null {
    if (this.rs || this.state !== 'open' || this.isLost) return this.rs;
    const stream: ReadableStream<Uint8Array> = new ReadableStream({
      type: 'bytes',
      start: (c) => { this.rc = c; },
      cancel: () => { if (this.rs === stream) this.rs = this.rc = null; },
    });
    this.rs = stream;
    for (const bytes of this.held.splice(0)) this.received(bytes);
    return stream;
  }

  get writable(): WritableStream<Uint8Array> | null {
    if (this.ws || this.state !== 'open' || this.isLost) return this.ws;
    const stream: WritableStream<Uint8Array> = new WritableStream<Uint8Array>({
      start: (c) => { this.wc = c; },
      write: (chunk) => {
        if (!(chunk instanceof ArrayBuffer) && !ArrayBuffer.isView(chunk)) throw new TypeError('Can only write an ArrayBuffer or ArrayBufferView.');
        if (!this.socket) throw lost();
        const view = chunk instanceof ArrayBuffer ? new Uint8Array(chunk) : new Uint8Array(chunk.buffer, chunk.byteOffset, chunk.byteLength);
        this.socket.send(view.slice());
      },
      close: () => { if (this.ws === stream) this.ws = this.wc = null; },
      abort: () => { if (this.ws === stream) this.ws = this.wc = null; },
    });
    this.ws = stream;
    return stream;
  }

  async close(): Promise<void> {
    if (this.state !== 'open') throw new DOMException('The port is already closed.', 'InvalidStateError');
    if (this.rs?.locked || this.ws?.locked) throw new TypeError("Failed to execute 'close' on 'SerialPort': Cannot cancel a locked stream");
    await this.rs?.cancel();
    await this.ws?.close();
    const socket = this.socket;
    this.socket = null;
    socket?.close(1000);
    this.rs = this.rc = this.ws = this.wc = null;
    this.held = [];
    this.state = 'closed';
  }

  /** The device went away (the relay closed, or the endpoint stopped listening). */
  lose(): void {
    if (this.isLost) return;
    this.isLost = true;
    this.socket = null;
    this.rc?.error(lost());
    this.wc?.error(lost());
    this.rs = this.rc = this.ws = this.wc = null;
    this.held = [];
    this.dispatchEvent(new Event('disconnect'));
    this.owner.dispatchEvent(new Event('disconnect'));
  }

  private received(bytes: Uint8Array<ArrayBuffer>): void {
    if (this.rc) this.rc.enqueue(bytes as Uint8Array<ArrayBuffer>); else this.held.push(bytes);
  }
}

export class FakeSerial extends EventTarget {
  /** How many times the chooser was shown. */
  chooser = 0;
  /** Every XY Forever port object handed out, oldest first (a new one after each restart). */
  readonly xyfPorts: FakePort[] = [];
  private readonly relay: string;
  private readonly adapter: FakePort;
  private grantedXyf = false;
  private readonly requireGesture: boolean;

  /** The USB ids XY Forever's port shows: the product build's composite unless a test picks the stock serial identity. */
  private readonly identity: PortInfo;

  constructor(relay: string, options: { requireGesture?: boolean; identity?: PortInfo } = {}) {
    super();
    this.relay = relay;
    this.identity = { ...(options.identity ?? USB_FILTERS[0]) };
    this.adapter = new FakePort(this, ADAPTER, null);
    this.requireGesture = options.requireGesture ?? true;
  }

  /** Makes this navigator.serial for the page; returns the function that undoes it. */
  install(): () => void {
    Object.defineProperty(navigator, 'serial', { value: this, configurable: true });
    return () => { delete (navigator as { serial?: unknown }).serial; };
  }

  async requestPort(options: { filters?: Filter[] } = {}): Promise<FakePort> {
    checkFilters(options.filters);
    if (this.requireGesture && !navigator.userActivation.isActive) {
      throw new DOMException("Failed to execute 'requestPort' on 'Serial': Must be handling a user gesture to show a permission request.", 'SecurityError');
    }
    this.chooser++;
    const xyf = await this.xyfPort();
    const port = [xyf, this.adapter].find((p) => p && matches(p.getInfo(), options.filters));
    if (!port) throw new DOMException('No port selected by the user.', 'NotFoundError');
    if (port === xyf) this.grantedXyf = true;
    return port;
  }

  async getPorts(): Promise<FakePort[]> {
    const xyf = this.grantedXyf ? await this.xyfPort() : null;
    return xyf ? [this.adapter, xyf] : [this.adapter];
  }

  /** XY Forever's port while the simulator's endpoint listens; a new object after it went away. */
  private async xyfPort(): Promise<FakePort | null> {
    let attached = false;
    try {
      const res = await fetch(this.relay.replace(/^ws/, 'http') + '/status', { cache: 'no-store' });
      attached = (await res.json() as { attached?: boolean }).attached === true;
    } catch { /* the relay is gone: not attached */ }
    let port = this.xyfPorts.at(-1);
    if (!attached) { port?.lose(); return null; }
    if (!port || port.isLost) {
      port = new FakePort(this, { ...this.identity }, this.relay);
      if (this.xyfPorts.length) this.dispatchEvent(new Event('connect'));
      this.xyfPorts.push(port);
    }
    return port;
  }
}
