// Types for the parts of core.mjs (Open-XY v0.0.7, MIT) the web app uses: opening a kit and its finished content.
export const PROFILE_SHA256: string;
export function decode64(text: string): Uint8Array<ArrayBuffer>;
/** The kit checked as Open-XY's builder checks it (the reviewed profile, every object's hash), its objects read. */
export function openKit<T extends object>(kit: T): Promise<T>;
export function assignNumbers(kit: object, selection: readonly string[], layout: null): Record<string, unknown>;
/** The selection's finished content: {path: bytes}, the extended content/op1_factory.json first. */
export function buildAssets(
  kit: object,
  selection: readonly string[],
  catalog: Uint8Array,
  numbers: Record<string, unknown>,
  options?: object,
  extensionIds?: readonly string[],
): Promise<Record<string, Uint8Array>>;
