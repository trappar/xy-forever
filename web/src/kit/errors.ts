/** A kit or package the browser refuses (modkit.py KitError); the message says why. */
export class KitError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'KitError';
  }
}

export function check(ok: unknown, message: string): asserts ok {
  if (!ok) throw new KitError(message);
}
