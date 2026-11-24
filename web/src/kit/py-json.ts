// Python's json.dumps for the kit's JSON values (xy_forever/modkit.py writes with it): the same bytes for the
// same value. The kit's numbers are integers or non-integral floats (an integral float reads back as an integer).
export type Json = null | boolean | number | string | readonly Json[] | { readonly [key: string]: Json };

export interface DumpOptions {
  /** sort_keys=True */
  readonly sortKeys?: boolean;
  /** indent=N: one item per line; else ', ' and ': ' separators */
  readonly indent?: number;
  /** separators=(',', ':') without indent */
  readonly compact?: boolean;
  /** ensure_ascii=True: every non-ASCII code unit as \uXXXX */
  readonly ensureAscii?: boolean;
}

/** repr(float): Python's shortest round trip, with its own exponent form and a .0 on integral values. */
export function pyFloat(value: number): string {
  if (!Number.isFinite(value)) throw new Error('JSON number out of range');
  const abs = Math.abs(value);
  if (abs !== 0 && (abs < 1e-4 || abs >= 1e16)) {
    const [mantissa, exponent] = value.toExponential().split('e') as [string, string];
    const power = Number(exponent);
    return `${mantissa}e${power < 0 ? '-' : '+'}${String(Math.abs(power)).padStart(2, '0')}`;
  }
  const text = String(value);
  return Number.isInteger(value) ? `${text}.0` : text;
}

function string(value: string, ascii: boolean): string {
  const text = JSON.stringify(value);
  return ascii ? text.replace(/[\u007f-\uffff]/g, (c) => `\\u${c.charCodeAt(0).toString(16).padStart(4, '0')}`) : text;
}

export function pyDumps(value: Json, options: DumpOptions = {}): string {
  const { sortKeys = false, indent, compact = false, ensureAscii = true } = options;
  const item = indent !== undefined || compact ? ',' : ', ',
    key = compact ? ':' : ': ';
  const dump = (v: Json, depth: number): string => {
    if (v === null) return 'null';
    if (typeof v === 'boolean') return v ? 'true' : 'false';
    if (typeof v === 'number') return Number.isInteger(v) ? String(v) : pyFloat(v);
    if (typeof v === 'string') return string(v, ensureAscii);
    const list = Array.isArray(v);
    const entries: string[] = list
      ? (v as readonly Json[]).map((x) => dump(x, depth + 1))
      : (sortKeys ? Object.keys(v).sort() : Object.keys(v)).map(
          (k) => `${string(k, ensureAscii)}${key}${dump((v as Record<string, Json>)[k]!, depth + 1)}`,
        );
    const [open, close] = list ? ['[', ']'] : ['{', '}'];
    if (!entries.length) return open + close;
    if (indent === undefined) return open + entries.join(item) + close;
    const inner = '\n' + ' '.repeat(indent * (depth + 1)),
      outer = '\n' + ' '.repeat(indent * depth);
    return open + inner + entries.join(item + inner) + outer + close;
  };
  return dump(value, 0);
}
