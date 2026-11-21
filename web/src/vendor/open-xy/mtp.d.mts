// Types for mtp.mjs (Open-XY, MIT): MTP over WebUSB for the dev menu's Mac file transfer mode.
export const BUSY: string;
export function supported(): boolean;
export interface SendOptions {
  replace?: (path: string) => Promise<boolean>;
  onProgress?: (fraction: number) => void;
  onStep?: (kind: 'folder' | 'replace', path: string) => void;
}
export class Device {
  static request(usb?: unknown): Promise<Device>;
  constructor(device: unknown);
  open(): Promise<void>;
  space(): Promise<{ total: number; free: number }>;
  size(path: string): Promise<number | null>;
  read(path: string, options?: { maxBytes?: number; onProgress?: (fraction: number) => void }): Promise<Uint8Array>;
  send(path: string, bytes: Uint8Array, options?: SendOptions): Promise<boolean>;
  close(): Promise<void>;
}
