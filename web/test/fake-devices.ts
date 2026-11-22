// Fakes for the install tests (unit tests in Node and the browser test): te-boot over Web MIDI and the dev menu's
// Mac MTP over WebUSB, as far as Open-XY's ferret.mjs and mtp.mjs use them. No DOM and no Node imports.
// - FakeMidi: requestMIDIAccess with one "OP-XY" input and output. It answers te-boot's identity request (0x09 ->
//   0x0a, the given fields) and challenge request (0x1f -> 0x20, 16 bytes), and records every frame it receives
//   (sent: {type, payload}); the dev-menu command 0xac gets no reply, as on the device.
// - FakeUsb: navigator.usb with one OP-XY in "mtp file transfer (mac)" mode: a storage with folders and files
//   (files: path -> bytes), free/total bytes, and the MTP operations mtp.mjs sends.
import { decode, encode } from '../src/vendor/open-xy/ferret.mjs';

export interface MidiFrame {
  type: number;
  payload: Uint8Array;
}

export class FakeMidi {
  readonly sent: MidiFrame[] = [];
  fields: Record<string, string>;
  constructor(fields: Record<string, string>) {
    this.fields = fields;
  }

  readonly requestMIDIAccess = async (options: { sysex: boolean }): Promise<unknown> => {
    if (!options.sysex) throw new DOMException('SysEx needed', 'SecurityError');
    const listeners = new Set<(event: { data: Uint8Array }) => void>();
    const input = {
      id: 'in-1',
      name: 'OP-XY',
      manufacturer: 'teenage engineering',
      state: 'connected',
      connection: 'closed',
      open: async () => input,
      close: async () => input,
      addEventListener: (_: string, f: (event: { data: Uint8Array }) => void) => listeners.add(f),
      removeEventListener: (_: string, f: (event: { data: Uint8Array }) => void) => listeners.delete(f),
    };
    const reply = (type: number, payload: Uint8Array): void => {
      const data = encode(type, payload);
      setTimeout(() => {
        for (const f of listeners) f({ data });
      }, 1);
    };
    const output = {
      id: 'out-1',
      name: 'OP-XY',
      manufacturer: 'teenage engineering',
      state: 'connected',
      connection: 'closed',
      open: async () => output,
      close: async () => output,
      send: (data: Uint8Array) => {
        const frame = decode(Uint8Array.from(data));
        if (!frame) throw new Error('fake MIDI: not a te-boot frame');
        this.sent.push({ type: frame.type, payload: frame.payload });
        if (frame.type === 0x09)
          reply(
            0x0a,
            new TextEncoder().encode(
              Object.entries(this.fields)
                .map(([k, v]) => `${k}:${v}`)
                .join(';'),
            ),
          );
        if (frame.type === 0x1f)
          reply(
            0x20,
            Uint8Array.from({ length: 16 }, (_, i) => i * 7 + 3),
          );
      },
    };
    return {
      inputs: new Map([[input.id, input]]),
      outputs: new Map([[output.id, output]]),
      addEventListener() {},
      removeEventListener() {},
    };
  };
}

interface MtpObject {
  name: string;
  parent: number;
  folder: boolean;
  data: Uint8Array;
}
const ROOT = 0xffffffff,
  STORAGE = 0x00010001,
  OK = 0x2001;

export class FakeUsb {
  readonly objects = new Map<number, MtpObject>();
  free: number;
  total = 512 * 1024 * 1024;
  requested = 0;
  /** The names of the objects read (GetObject), in order: the page never reads the stock os.ldr. */
  readonly reads: string[] = [];
  private next = 1;
  constructor(files: Record<string, Uint8Array>, free: number) {
    this.free = free;
    for (const [path, data] of Object.entries(files)) this.put(path, data);
  }

  /** The object under parent named name. */
  find(path: string): MtpObject | undefined {
    let parent = ROOT,
      found: MtpObject | undefined;
    for (const name of path.split('/')) {
      found = [...this.objects].find(([, o]) => o.parent === parent && o.name === name)?.[1];
      if (!found) return undefined;
      parent = [...this.objects].find(([, o]) => o === found)![0];
    }
    return found;
  }

  private put(path: string, data: Uint8Array): void {
    let parent = ROOT;
    const names = path.split('/');
    names.forEach((name, i) => {
      const last = i === names.length - 1;
      const have = [...this.objects].find(([, o]) => o.parent === parent && o.name === name);
      if (have) {
        parent = have[0];
        return;
      }
      const handle = this.next++;
      this.objects.set(handle, { name, parent, folder: !last, data: last ? data : new Uint8Array(0) });
      parent = handle;
    });
  }

  readonly requestDevice = async (): Promise<unknown> => {
    this.requested++;
    return this.device();
  };

  private device(): unknown {
    const out: Uint8Array[] = [];
    let pending: Uint8Array = new Uint8Array(0),
      command: { code: number; id: number; params: number[] } | null = null;
    let sendTarget: number | null = null;
    const container = (type: number, code: number, id: number, payload: Uint8Array): Uint8Array => {
      const b = new Uint8Array(12 + payload.length),
        v = new DataView(b.buffer);
      v.setUint32(0, b.length, true);
      v.setUint16(4, type, true);
      v.setUint16(6, code, true);
      v.setUint32(8, id, true);
      b.set(payload, 12);
      return b;
    };
    const words = (values: number[]): Uint8Array => {
      const b = new Uint8Array(values.length * 4),
        v = new DataView(b.buffer);
      values.forEach((x, i) => v.setUint32(i * 4, x, true));
      return b;
    };
    const string = (text: string): Uint8Array => {
      const units = text + '\0',
        b = new Uint8Array(1 + units.length * 2),
        v = new DataView(b.buffer);
      b[0] = units.length;
      for (let i = 0; i < units.length; i++) v.setUint16(1 + i * 2, units.charCodeAt(i), true);
      return b;
    };
    const respond = (id: number, code = OK, params: number[] = []): void => {
      out.push(container(3, code, id, words(params)));
    };
    const data = (cmd: { code: number; id: number }, payload: Uint8Array): void => {
      out.push(container(2, cmd.code, cmd.id, payload));
    };
    const run = (cmd: { code: number; id: number; params: number[] }, payload: Uint8Array | null): void => {
      const [p0 = 0, p1 = 0, p2 = 0] = cmd.params;
      switch (cmd.code) {
        case 0x1002:
        case 0x1003:
          respond(cmd.id);
          break;
        case 0x1004:
          data(cmd, words([1, STORAGE]));
          respond(cmd.id);
          break;
        case 0x1005: {
          const info = new Uint8Array(26),
            v = new DataView(info.buffer);
          v.setBigUint64(6, BigInt(this.total), true);
          v.setBigUint64(14, BigInt(this.free), true);
          data(cmd, info);
          respond(cmd.id);
          break;
        }
        case 0x1007: {
          const handles = [...this.objects].filter(([, o]) => o.parent === p2).map(([h]) => h);
          data(cmd, words([handles.length, ...handles]));
          respond(cmd.id);
          break;
        }
        case 0x1008: {
          const o = this.objects.get(p0)!;
          const fixed = new Uint8Array(52),
            v = new DataView(fixed.buffer);
          v.setUint32(0, STORAGE, true);
          v.setUint16(4, o.folder ? 0x3001 : 0x3000, true);
          v.setUint32(8, o.data.length, true);
          v.setUint32(38, o.parent, true);
          const name = string(o.name),
            all = new Uint8Array(52 + name.length);
          all.set(fixed);
          all.set(name, 52);
          data(cmd, all);
          respond(cmd.id);
          break;
        }
        case 0x1009:
          this.reads.push(this.objects.get(p0)!.name);
          data(cmd, this.objects.get(p0)!.data);
          respond(cmd.id);
          break;
        case 0x100b: {
          const o = this.objects.get(p0)!;
          this.free += o.data.length;
          this.objects.delete(p0);
          respond(cmd.id);
          break;
        }
        case 0x100c: {
          if (!payload) return;
          const v = new DataView(payload.buffer, payload.byteOffset, payload.byteLength);
          const folder = v.getUint16(4, true) === 0x3001,
            size = v.getUint32(8, true);
          let name = '';
          for (let i = 0; i < payload[52]!; i++) name += String.fromCharCode(v.getUint16(53 + i * 2, true));
          name = name.replace(/\0$/, '');
          if (!folder && size > this.free) {
            respond(cmd.id, 0x200c);
            return;
          }
          const handle = this.next++;
          this.objects.set(handle, { name, parent: p1, folder, data: new Uint8Array(0) });
          if (!folder) sendTarget = handle;
          respond(cmd.id, OK, [STORAGE, p1, handle]);
          break;
        }
        case 0x100d: {
          if (!payload) return;
          this.objects.get(sendTarget!)!.data = payload.slice();
          this.free -= payload.length;
          respond(cmd.id);
          break;
        }
        default:
          respond(cmd.id, 0x2005);
      }
    };
    const receive = (bytes: Uint8Array): void => {
      if (bytes.length === 0) return;
      const all = new Uint8Array(pending.length + bytes.length);
      all.set(pending);
      all.set(bytes, pending.length);
      pending = all;
      const v = new DataView(pending.buffer);
      if (pending.length < 12 || pending.length < v.getUint32(0, true)) return;
      const type = v.getUint16(4, true),
        code = v.getUint16(6, true),
        id = v.getUint32(8, true),
        body = pending.slice(12, v.getUint32(0, true));
      pending = new Uint8Array(0);
      if (type === 1) {
        command = {
          code,
          id,
          params: [...Array(body.length / 4).keys()].map((i) => new DataView(body.buffer).getUint32(i * 4, true)),
        };
        if (code !== 0x100c && code !== 0x100d) {
          run(command, null);
          command = null;
        }
      } else if (type === 2 && command) {
        run(command, body);
        command = null;
      }
    };
    const ok = (extra: Record<string, unknown> = {}): Record<string, unknown> => ({ status: 'ok', ...extra });
    const device = {
      opened: false,
      configuration: null as unknown,
      open: async () => {
        device.opened = true;
      },
      close: async () => {
        device.opened = false;
      },
      selectConfiguration: async () => {
        device.configuration = {
          interfaces: [
            {
              interfaceNumber: 0,
              alternate: {
                interfaceClass: 0xff,
                endpoints: [
                  { type: 'bulk', direction: 'in', endpointNumber: 1, packetSize: 512 },
                  { type: 'bulk', direction: 'out', endpointNumber: 2, packetSize: 512 },
                ],
              },
            },
          ],
        };
      },
      claimInterface: async () => {},
      releaseInterface: async () => {},
      clearHalt: async () => {},
      controlTransferOut: async () => ok(),
      controlTransferIn: async () => ok({ data: new DataView(new Uint8Array([4, 0, 1, 0x20]).buffer) }),
      transferOut: async (_: number, bytes: Uint8Array) => {
        receive(Uint8Array.from(bytes));
        return ok({ bytesWritten: bytes.length });
      },
      transferIn: async (_: number, length: number) => {
        const head = out[0];
        if (!head) throw new Error('fake MTP: nothing to read');
        const part = head.slice(0, length);
        if (part.length === head.length) out.shift();
        else out[0] = head.slice(length);
        return ok({ data: new DataView(part.buffer) });
      },
    };
    return device;
  }
}
