// Types for ferret.mjs (Open-XY's copy of Ferret, MIT): te-boot over Web MIDI SysEx.
export interface Frame { seq: number; type: number; payload: Uint8Array }
export function encode(cmd: number, payload?: Uint8Array): Uint8Array;
export function decode(data: Uint8Array): Frame | null;
export function enablePayload(otp: Uint8Array): Uint8Array;
/** te-boot's identity reply (command 0x09): product, serial, bl_version, os_version, sw_version, mode, ... */
export type Identity = Record<string, string>;
export interface PortDescription { name: string; manufacturer: string; state: string; connection: string }
export interface SessionOptions {
  accessTimeout?: number;
  portTimeout?: number;
  onProgress?: (text: string) => void;
  onPorts?: (ports: { inputs: PortDescription[]; outputs: PortDescription[] }) => void;
}
export class Session {
  constructor(requestAccess: (options: { sysex: boolean }) => Promise<unknown>, onDisconnect?: () => void, timeout?: number, options?: SessionOptions);
  connect(): Promise<Identity>;
  /** Sends the dev-menu enable command (0xac). There is no acknowledgement: only the screen confirms it. */
  enable(): Promise<void>;
  close(): void;
}
