// The parts of Open-XY's builder (core.mjs, MIT) this app calls, loaded at run time from the kit's own release
// (source.ts loadCore); the vendored v0.0.7 copy (src/vendor/open-xy/) is only the tests' and offline reference.

/** An object as core.mjs readObject reads it (openKit adds this to every kit object). */
export interface ReadObject {
  readonly sections: readonly { readonly size: number; readonly group?: string | null }[];
  readonly symbols: readonly {
    readonly name: string;
    readonly binding: number;
    readonly type: number;
    readonly section: number;
  }[];
}

export interface Core {
  /** The kit checked as Open-XY's builder checks it, every object read (readObject). */
  openKit<T extends object>(kit: T): Promise<T>;
  /** The profile carries a reviewed 4 MiB layout (experimental_memory). */
  offersExpandedMemory(profile: object): boolean;
  decode64(text: string): Uint8Array<ArrayBuffer>;
  assignNumbers(kit: object, selection: readonly string[], layout: null): Record<string, unknown>;
  /** The selection's finished content: {path: bytes}, with the extended content/op1_factory.json. */
  buildAssets(
    kit: object,
    selection: readonly string[],
    catalog: Uint8Array,
    numbers: Record<string, unknown>,
    options?: object,
    extensionIds?: readonly string[],
  ): Promise<Record<string, Uint8Array>>;
}
