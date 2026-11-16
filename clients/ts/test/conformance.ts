// Conformance runner: the cases of protocol/v1.conformance.json, run with the generated module's
// codecs (src/v1.ts) through the call layer's link (src/link.ts) and TCP transport (src/tcp.ts)
// against the device simulator (plan 1.4; decision 013). Same data and meaning as the Python
// runner (xy_forever/conformance.py, scripts/conformance.py); validation and coverage live there.
// test/conformance.spec.ts runs it under Vitest; this file also runs alone under Node.
//
//   node test/conformance.ts --check                  interpret every case (no simulator)
//   node test/conformance.ts --sim 127.0.0.1:7341     run every case against a running simulator
// Options: --only REGEX, --fixture NAME=PATH, --cases FILE. Exit: 0 nothing failed, 1 a case failed,
// 2 bad arguments or case data, 3 NEED (no simulator). Node 22.18+/23.6+ (type stripping).
import { createHash } from 'node:crypto';
import { readFileSync } from 'node:fs';
import * as net from 'node:net';
import { pathToFileURL } from 'node:url';
import { isXyfError } from '../src/errors.ts';
import { Link } from '../src/link.ts';
import { TcpTransport } from '../src/tcp.ts';
import * as v1 from '../src/v1.ts';

type Json = null | boolean | number | string | Json[] | { [key: string]: Json };
type Obj = { [key: string]: Json };
type Val = number | string | Uint8Array;
type Want = 'int' | 'str' | 'bytes' | 'any';

export class CaseError extends Error {}
class Fail extends Error {}
class Skip extends Error {}
const DRY = Symbol('dry');                     // a value known only while running (check mode)

const HERE = new URL('.', import.meta.url);
const SPEC = JSON.parse(readFileSync(new URL('../../../protocol/v1.json', HERE), 'utf-8')) as Obj;
const ALL_TYPES = new Set((SPEC['commands'] as Obj[]).map((c) => parseInt(c['type'] as string, 16)));
const PUBLISHED = new Set(Object.keys(v1.COMMANDS));
const CODECS = { ...v1.codecs, ...v1.dev } as unknown as Record<string, { encodeRequest(v?: unknown): Uint8Array }>;
const REPLY = v1.REPLY;
const EVENT_LOG = v1.TYPE.log;

const ACTIONS = ['send', 'send_type', 'send_bytes', 'stream', 'each_type', 'stage', 'write', 'disconnect', 'reconnect', 'sim'];
const STEP_COMMON = new Set(['expect', 'repeat', 'seq', 'doc', 'fields', 'payload']);
const EXPECTS: Record<string, string[]> = {
  send: ['status', 'alone', 'fields', 'hello', 'save', 'events'],
  send_type: ['status', 'alone'],
  each_type: ['status', 'alone'],
  send_bytes: ['replies', 'none_within_ms'],
};
const KEYS: Record<string, [string[], string[]]> = {
  case: [['name', 'doc', 'effects', 'real_device', 'steps'], ['rules', 'variant', 'needs', 'why_not', 'given']],
  given: [[], ['boot', 'hello', 'files', 'faults']],
  file: [['path', 'data'], []],
  repeat: [['var', 'from', 'to', 'step'], []],
  expect: [[], ['status', 'alone', 'fields', 'hello', 'save', 'events', 'replies', 'none_within_ms']],
  hello: [[], ['prefix', 'keys', 'lacks', 'answers', 'pre_v1']],
  reply: [['type', 'seq'], ['status']],
  reconnect: [[], ['hello']],
  disconnect: [[], []],
  write: [['path', 'data'], []],
  events: [['log'], []],
  count: [['min'], []],
  frame: [['type', 'seq'], ['payload']],
};
const FORMS: [string, string[]][] = [
  ['prefix', ['prefix', 'fill', 'length']], ['text', ['text']], ['hex', ['hex']], ['fill', ['fill', 'count']],
  ['ramp', ['ramp']], ['blob', ['blob', 'from', 'length']], ['size', ['size', 'plus']], ['crc32', ['crc32']],
  ['sha256', ['sha256']], ['const', ['const', 'plus']], ['hello', ['hello', 'plus']], ['var', ['var', 'plus']],
  ['frame', ['frame', 'from', 'length']], ['concat', ['concat']],
];
const CONSTRAINTS = new Set(['len', 'len_min', 'len_max', 'min', 'max', 'matches', 'one_of']);
const FIXTURES = ['stock_os_ldr', 'xyf_build'];

function isObj(v: Json | undefined): v is Obj {
  return typeof v === 'object' && v !== null && !Array.isArray(v);
}
function checkKeys(v: Json | undefined, kind: string, where: string): Obj {
  if (!isObj(v)) throw new CaseError(`${where}: must be an object`);
  const [required, optional] = KEYS[kind]!;
  const missing = required.filter((k) => !(k in v));
  const unknown = Object.keys(v).filter((k) => !required.includes(k) && !optional.includes(k));
  if (missing.length || unknown.length) throw new CaseError(`${where}: missing [${missing}] unknown [${unknown}]`);
  return v;
}
function int(v: Json | undefined): v is number {
  return typeof v === 'number' && Number.isInteger(v);
}
function hex(text: Json | undefined, where: string): Uint8Array {
  if (typeof text !== 'string' || !/^([0-9a-f]{2})*$/.test(text)) throw new CaseError(`${where}: not lowercase hex`);
  return v1.fromHex(text);
}
function concat(parts: Uint8Array[]): Uint8Array {
  const out = new Uint8Array(parts.reduce((n, p) => n + p.length, 0));
  let at = 0;
  for (const p of parts) { out.set(p, at); at += p.length; }
  return out;
}
function utf8(text: string): Uint8Array {
  return new TextEncoder().encode(text);
}
function sha256(data: Uint8Array): Uint8Array {
  return new Uint8Array(createHash('sha256').update(data).digest());
}
function equal(a: Val, b: Val): boolean {
  if (a instanceof Uint8Array && b instanceof Uint8Array) return a.length === b.length && a.every((x, i) => x === b[i]);
  return a === b;
}
function show(v: Val): string {
  return v instanceof Uint8Array ? v1.toHex(v.subarray(0, 32)) + (v.length > 32 ? `... (${v.length} B)` : '') : JSON.stringify(v);
}
function constant(name: string): number {
  const value = (v1 as unknown as Record<string, unknown>)[name.toUpperCase()];
  if (typeof value !== 'number') throw new CaseError(`unknown constant ${name}`);
  return value;
}
function helloKeys(text: string): Record<string, string> {
  const keys: Record<string, string> = {};
  for (const item of text.split(';')) {
    const at = item.indexOf('=');
    if (at >= 0) keys[item.slice(0, at)] = item.slice(at + 1);
  }
  return keys;
}

// ---- values -----------------------------------------------------------------------------------

class Context {
  data: Obj;
  dry: boolean;
  fixtures: Record<string, string>;
  vars: Record<string, Val> = {};
  helloText: string | null = null;
  blobs = new Map<string, Uint8Array>();
  constructor(data: Obj, fixtures: Record<string, string>, dry: boolean) {
    this.data = data; this.fixtures = fixtures; this.dry = dry;
  }

  blob(name: Json | undefined): Uint8Array {
    const blobs = this.data['blobs'] as Obj;
    if (typeof name !== 'string' || !isObj(blobs[name])) throw new CaseError(`unknown blob ${String(name)}`);
    let out = this.blobs.get(name);
    if (out === undefined) {
      const b = blobs[name] as Obj;
      if ('fixture' in b) {
        if (!FIXTURES.includes(b['fixture'] as string)) throw new CaseError(`blob ${name}: unknown fixture`);
        const path = this.fixtures[b['fixture'] as string];
        if (path === undefined) {
          if (!this.dry) throw new Skip(`needs fixture ${String(b['fixture'])} (--fixture ${String(b['fixture'])}=PATH)`);
          out = new Uint8Array(0);
        } else out = new Uint8Array(readFileSync(path));
      } else out = bytesForm(b, `blob ${name}`);
      this.blobs.set(name, out);
    }
    return out;
  }

  helloKey(key: string): string | typeof DRY {
    if (this.dry) return DRY;
    if (this.helloText === null) throw new Fail(`no hello reply yet: {hello: ${key}} has no value`);
    const value = helloKeys(this.helloText)[key];
    if (value === undefined) throw new Fail(`the hello reply has no key ${key}`);
    return value;
  }
}

function bytesForm(v: Obj, where: string): Uint8Array {
  const keys = Object.keys(v).sort().join(',');
  if (keys === 'text' && typeof v['text'] === 'string') return utf8(v['text']);
  if (keys === 'hex') return hex(v['hex'], where);
  if (keys === 'count,fill' && int(v['fill']) && v['fill'] >= 0 && v['fill'] <= 255 && int(v['count']) && v['count'] >= 0) {
    return new Uint8Array(v['count']).fill(v['fill']);
  }
  if (keys === 'ramp' && int(v['ramp']) && v['ramp'] >= 0) return Uint8Array.from({ length: v['ramp'] }, (_, i) => i & 0xff);
  throw new CaseError(`${where}: not a blob form`);
}

function formOf(v: Json | undefined): string | null {
  if (!isObj(v)) return null;
  const keys = Object.keys(v);
  if (keys.some((k) => CONSTRAINTS.has(k))) {
    if (keys.some((k) => !CONSTRAINTS.has(k))) throw new CaseError(`constraint with unknown keys ${keys}`);
    return null;
  }
  for (const [primary, allowed] of FORMS) {
    if (primary in v) {
      if (keys.some((k) => !allowed.includes(k))) throw new CaseError(`value {${primary}} with unknown keys ${keys}`);
      return primary;
    }
  }
  throw new CaseError(`unknown value form ${keys}`);
}

function resolve(v: Json | undefined, want: Want, ctx: Context, where = 'value'): Val {
  if (typeof v === 'number') {
    if (!Number.isInteger(v)) throw new CaseError(`${where}: not an integer`);
    if (want === 'int' || want === 'any') return v;
    if (want === 'str') return String(v);
    throw new CaseError(`${where}: a number where bytes are wanted`);
  }
  if (typeof v === 'string') {
    if (want === 'bytes') return hex(v, where);
    if (want === 'int') throw new CaseError(`${where}: ${v} where a number is wanted`);
    return v;
  }
  const form = formOf(v);
  if (form === null || !isObj(v)) throw new CaseError(`${where}: not a value`);
  const out = form_(v, form, ctx, where);
  if (out === DRY) return want === 'int' ? 0 : want === 'bytes' ? new Uint8Array(0) : '';
  if (typeof out === 'string' && want === 'bytes') return utf8(out);
  if (typeof out === 'string' && want === 'int') {
    if (!/^[0-9]+$/.test(out)) throw new Fail(`${where}: ${out} is not a number`);
    return parseInt(out, 10);
  }
  if (typeof out === 'number' && want === 'str') return String(out);
  if (out instanceof Uint8Array && (want === 'int' || want === 'str')) throw new CaseError(`${where}: bytes where a ${want} is wanted`);
  if (typeof out === 'number' && want === 'bytes') throw new CaseError(`${where}: a number where bytes are wanted`);
  return out;
}

function form_(v: Obj, form: string, ctx: Context, where: string): Val | typeof DRY {
  const plus = v['plus'] ?? 0;
  if (!int(plus)) throw new CaseError(`${where}: plus must be a number`);
  const slice = (data: Uint8Array): Uint8Array => {
    const start = resolve(v['from'] ?? 0, 'int', ctx, where) as number;
    const end = 'length' in v ? start + (resolve(v['length'], 'int', ctx, where) as number) : data.length;
    return data.slice(start, end);
  };
  switch (form) {
    case 'prefix': {
      const length = resolve(v['length'], 'int', ctx, where) as number;
      const prefix = v['prefix'], fill = v['fill'];
      if (typeof prefix !== 'string' || typeof fill !== 'string' || utf8(fill).length !== 1) {
        throw new CaseError(`${where}: prefix is text, fill one ASCII character`);
      }
      return prefix + fill.repeat(Math.max(0, length - utf8(prefix).length));
    }
    case 'text':
      if (typeof v['text'] !== 'string') throw new CaseError(`${where}: text must be a string`);
      return v['text'];
    case 'hex': case 'fill': case 'ramp': return bytesForm(v, where);
    case 'blob': return slice(ctx.blob(v['blob']));
    case 'size': return ctx.blob(v['size']).length + plus;
    case 'crc32': return v1.crc32(ctx.blob(v['crc32']));
    case 'sha256': return sha256(ctx.blob(v['sha256']));
    case 'const': return constant(String(v['const'])) + plus;
    case 'hello': {
      const value = ctx.helloKey(String(v['hello']));
      if (plus && value !== DRY) {
        if (!/^[0-9]+$/.test(value)) throw new Fail(`${where}: hello ${String(v['hello'])}=${value} is not a number`);
        return parseInt(value, 10) + plus;
      }
      return value;
    }
    case 'var': {
      if (ctx.dry) return DRY;
      const value = ctx.vars[String(v['var'])];
      if (value === undefined) throw new CaseError(`${where}: var ${String(v['var'])} is not set`);
      return plus ? (value as number) + plus : value;
    }
    case 'frame': {
      const f = checkKeys(v['frame'], 'frame', where);
      if (typeof f['type'] !== 'string' || !/^0x[0-9a-f]{2}$/.test(f['type']) || !int(f['seq']) || f['seq'] < 0 || f['seq'] > 255) {
        throw new CaseError(`${where}: frame type "0xNN", seq 0..255`);
      }
      return slice(v1.encodeFrame(parseInt(f['type'], 16), f['seq'], resolve(f['payload'] ?? '', 'bytes', ctx, where) as Uint8Array));
    }
    case 'concat':
      if (!Array.isArray(v['concat'])) throw new CaseError(`${where}: concat takes a list`);
      return concat(v['concat'].map((x) => resolve(x, 'bytes', ctx, where) as Uint8Array));
  }
  throw new CaseError(`${where}: form ${form}`);
}

function fieldWant(f: v1.Field): Want {
  return f.type === 'u8' || f.type === 'u16' || f.type === 'u32' ? 'int' : f.type === 'cstr' || f.type === 'text' ? 'str' : 'bytes';
}

// ---- requests ---------------------------------------------------------------------------------

interface Request { type: number; payload: Uint8Array; command: v1.Command | null }

function requestOf(step: Obj, ctx: Context, where: string): Request {
  if ('send' in step) {
    const name = step['send'];
    if (typeof name !== 'string' || !PUBLISHED.has(name)) throw new CaseError(`${where}: ${String(name)} is not a published command`);
    const command = v1.COMMANDS[name as v1.CommandName];
    if (('fields' in step) === ('payload' in step)) throw new CaseError(`${where}: send takes fields or payload`);
    if ('payload' in step) return { type: command.type, payload: resolve(step['payload'], 'bytes', ctx, where) as Uint8Array, command };
    const given = step['fields'];
    if (!isObj(given)) throw new CaseError(`${where}: fields must be an object`);
    const unknown = Object.keys(given).filter((k) => !command.request.fields.some((f) => f.name === k));
    if (unknown.length) throw new CaseError(`${where}: unknown field(s) ${unknown}`);
    const values: Record<string, Val> = {};
    for (const f of command.request.fields) {
      if (f.name in given) values[f.name] = resolve(given[f.name], fieldWant(f), ctx, `${where}.${f.name}`);
      else if (!f.optional) throw new CaseError(`${where}: field ${f.name} missing`);
    }
    try {
      return { type: command.type, payload: CODECS[name]!.encodeRequest(values), command };
    } catch (error) {
      throw new CaseError(`${where}: ${(error as Error).message}`);
    }
  }
  const kind = step['send_type'];
  if (typeof kind !== 'string' || !/^0x[0-7][0-9a-f]$/.test(kind) || ALL_TYPES.has(parseInt(kind, 16))) {
    throw new CaseError(`${where}: send_type is "0xNN" below 0x80 and no command`);
  }
  return { type: parseInt(kind, 16), payload: resolve(step['payload'] ?? '', 'bytes', ctx, where) as Uint8Array, command: null };
}

function actionOf(step: Json, where: string): string {
  if (!isObj(step)) throw new CaseError(`${where}: a step must be an object`);
  const actions = ACTIONS.filter((a) => a in step);
  if (actions.length !== 1) throw new CaseError(`${where}: a step has exactly one action`);
  const unknown = Object.keys(step).filter((k) => !STEP_COMMON.has(k) && !ACTIONS.includes(k));
  if (unknown.length) throw new CaseError(`${where}: unknown step key(s) ${unknown}`);
  const action = actions[0]!;
  if (action !== 'send' && action !== 'send_type' && ('fields' in step || 'payload' in step)) throw new CaseError(`${where}: fields/payload belong to send`);
  if ('expect' in step !== action in EXPECTS) throw new CaseError(`${where}: ${action} ${action in EXPECTS ? 'needs' : 'takes no'} expect`);
  if ('repeat' in step && action !== 'send' && action !== 'send_type') throw new CaseError(`${where}: repeat belongs to send`);
  return action;
}

function stageFrames(body: Uint8Array): Request[] {
  const out: Request[] = [{ type: v1.TYPE['stage.begin'], command: null,
    payload: v1.encodeStageBeginRequest({ size: body.length, crc32: v1.crc32(body), sha256: sha256(body) }) }];
  for (let at = 0; at < body.length; at += v1.PUT_CHUNK) {
    out.push({ type: v1.TYPE['stage.data'], command: null, payload: v1.encodeStageDataRequest({ offset: at, data: body.subarray(at, at + v1.PUT_CHUNK) }) });
  }
  out.push({ type: v1.TYPE['stage.end'], command: null, payload: new Uint8Array(0) });
  return out;
}

// Interpret a case without a simulator: every key, value, request and expectation (vars dry).
function interpret(c: Json, data: Obj, fixtures: Record<string, string>): string {
  const ctx = new Context(data, fixtures, true);
  const kase = checkKeys(c, 'case', 'case');
  const where = String(kase['name']);
  const given = checkKeys(kase['given'] ?? {}, 'given', `${where} given`);
  for (const f of (given['files'] ?? []) as Json[]) {
    const file = checkKeys(f, 'file', `${where} given.files`);
    resolve(file['path'], 'str', ctx, where);
    stageFrames(resolve(file['data'], 'bytes', ctx, where) as Uint8Array);
  }
  if (!Array.isArray(kase['steps'])) throw new CaseError(`${where}: steps must be a list`);
  kase['steps'].forEach((s, n) => {
    const at = `${where} step ${n + 1}`;
    const action = actionOf(s, at);
    const step = s as Obj;
    let command: v1.Command | null = null;
    if ('repeat' in step) {
      const r = checkKeys(step['repeat'], 'repeat', at);
      for (const k of ['from', 'to', 'step']) resolve(r[k], 'int', ctx, at);
    }
    if (action === 'send' || action === 'send_type') command = requestOf(step, ctx, at).command;
    else if (action === 'send_bytes') resolve(step['send_bytes'], 'bytes', ctx, at);
    else if (action === 'stream') {
      if (!v1.STREAMS.some((st) => st.name === step['stream'] && st.direction === 'to_device')) throw new CaseError(`${at}: unknown stream`);
    } else if (action === 'each_type') {
      if (step['each_type'] !== 'unlisted') throw new CaseError(`${at}: each_type is "unlisted"`);
    } else if (action === 'stage') stageFrames(resolve(step['stage'], 'bytes', ctx, at) as Uint8Array);
    else if (action === 'write') {
      const w = checkKeys(step['write'], 'write', at);
      resolve(w['path'], 'str', ctx, at);
      stageFrames(resolve(w['data'], 'bytes', ctx, at) as Uint8Array);
    } else if (action === 'disconnect' || action === 'reconnect') checkKeys(step[action], action, at);
    else if (action === 'sim' && typeof step['sim'] !== 'string') throw new CaseError(`${at}: sim is a control line`);
    if ('expect' in step) {
      const e = checkKeys(step['expect'], 'expect', at);
      const extra = Object.keys(e).filter((k) => !EXPECTS[action]!.includes(k));
      if (extra.length) throw new CaseError(`${at}: ${action} cannot expect ${extra}`);
      for (const s of ([] as Json[]).concat(e['status'] ?? [])) {
        if (typeof s !== 'string' || !(s in v1.STATUS)) throw new CaseError(`${at}: unknown status ${String(s)}`);
      }
      const fields = command ? command.reply.fields : [];
      for (const [name, value] of Object.entries(isObj(e['fields']) ? e['fields'] : {})) {
        const f = fields.find((x) => x.name === name);
        if (!f) throw new CaseError(`${at}: the reply has no field ${name}`);
        expected(value, fieldWant(f), ctx, at);
      }
      for (const name of Object.values(isObj(e['save']) ? e['save'] : {})) {
        if (!fields.some((x) => x.name === name)) throw new CaseError(`${at}: save from unknown field ${String(name)}`);
      }
      if ('hello' in e) {
        const h = checkKeys(e['hello'], 'hello', at);
        for (const value of Object.values(isObj(h['keys']) ? h['keys'] : {})) expected(value, 'str', ctx, at);
        for (const name of (h['answers'] ?? []) as Json[]) if (!PUBLISHED.has(String(name))) throw new CaseError(`${at}: answers ${String(name)}`);
      }
      if ('events' in e) checkKeys(checkKeys(e['events'], 'events', at)['log'], 'count', at);
      for (const r of (e['replies'] ?? []) as Json[]) checkKeys(r, 'reply', at);
    }
  });
  return where;
}

function expected(value: Json | undefined, want: Want, ctx: Context, where: string): void {
  if (isObj(value) && formOf(value) === null) {
    for (const [k, v] of Object.entries(value)) {
      if (k === 'matches') new RegExp(String(v));
      else if (k === 'one_of') (v as Json[]).forEach((x) => resolve(x, want, ctx, where));
      else resolve(v, 'int', ctx, where);
    }
  } else resolve(value, want, ctx, where);
}

// ---- the link ---------------------------------------------------------------------------------

// The call layer's link (src/link.ts) over the TCP transport (src/tcp.ts), with what the cases
// need on top: a log of the exchange, log events counted per request, failures as Fail.
class CaseLink {
  link: Link;
  events = 0;
  barrier = 0;
  constructor(link: Link) {
    this.link = link;
    link.onEvent((f) => { if (f.type === EVENT_LOG) this.events++; });
  }

  static async open(host: string, port: number, log: string[]): Promise<CaseLink> {
    const trace = (_dir: '<' | '>', d: v1.Frame | Uint8Array): void => {
      log.push(d instanceof Uint8Array ? `> ${d.length} B ${v1.toHex(d.subarray(0, 48))}${d.length > 48 ? '...' : ''}`
        : `< 0x${d.type.toString(16)} seq ${d.seq} ${d.payload.length} B ${v1.toHex(d.payload.subarray(0, 24))}`);
    };
    return new CaseLink(new Link(await TcpTransport.connect(host, port), trace));
  }

  async request(type: number, payload: Uint8Array, ms: number, seq?: number): Promise<{ payload: Uint8Array; events: number }> {
    const before = this.events;
    try {
      const reply = await this.link.request(type, payload, { timeoutMs: ms, seq });
      return { payload: reply, events: this.events - before };
    } catch (error) {
      if (!isXyfError(error)) throw error;
      if (error.code === 'timeout') throw new Fail(`no reply to 0x${type.toString(16)} within ${ms / 1000} s`);
      if (error.code === 'disconnected') throw new Fail('the connection closed');
      throw new Fail(error.message);
    }
  }

  async exchange(chunks: Uint8Array[], gapMs: number, ms: number, until: (frames: readonly v1.Frame[]) => boolean): Promise<v1.Frame[]> {
    try {
      return await this.link.exchange(chunks, { gapMs, timeoutMs: ms, until });
    } catch (error) {
      if (isXyfError(error)) throw new Fail(error.message);
      throw error;
    }
  }

  gone(ms: number): Promise<boolean> {
    return this.link.waitClosed(ms);
  }

  close(): Promise<void> {
    return this.link.close();
  }
}

// ---- running ----------------------------------------------------------------------------------

class Runner {
  data: Obj;
  host: string;
  port: number;
  fixtures: Record<string, string>;
  timing: Obj;
  helloText: string | null = null;
  lines: string[] = [];
  ctx: Context;
  capMs = 5000;

  constructor(data: Obj, host: string, port: number, fixtures: Record<string, string>) {
    this.data = data; this.host = host; this.port = port; this.fixtures = fixtures;
    this.timing = data['timing'] as Obj;
    this.ctx = new Context(data, fixtures, false);
  }

  get preV1(): boolean {
    return !/^[0-9a-f]{32}$/.test(helloKeys(this.helloText ?? '')['cmds'] ?? '');
  }

  async open(hello = true, waitMs = this.timing['back_ms'] as number): Promise<CaseLink> {
    const deadline = Date.now() + waitMs;
    for (;;) {
      try {
        const link = await CaseLink.open(this.host, this.port, this.lines);
        this.lines.push('# connected');
        if (hello) await this.hello(link).catch(async (error: unknown) => { await link.close(); throw error; });
        return link;
      } catch (error) {
        if (error instanceof Fail || Date.now() > deadline) throw error instanceof Fail ? error : new Fail(`the port did not come back within ${waitMs} ms`);
        await new Promise((ok) => setTimeout(ok, 50));
      }
    }
  }

  async hello(link: CaseLink): Promise<string> {
    const reply = v1.decodeHelloReply((await link.request(v1.TYPE.hello, new Uint8Array(0), this.capMs)).payload);
    if (!reply.ok) throw new Fail(`hello: ${v1.statusName(reply.status)}`);
    this.helloText = this.ctx.helloText = reply.fields.info;
    return reply.fields.info;
  }

  async control(line: string): Promise<void> {
    const reply = await new Promise<string>((ok, fail) => {
      const sock = net.createConnection({ host: this.host, port: this.port + 1 });
      let text = '';
      sock.on('connect', () => sock.write(line + '\n'));
      sock.on('data', (d: Buffer) => { text += d.toString(); if (text.endsWith('\n')) { sock.destroy(); ok(text); } });
      sock.on('error', fail);
      sock.on('close', () => ok(text));
    });
    const parsed = JSON.parse(reply || '{}') as Obj;
    if (parsed['ok'] === false) throw new Fail(`simulator control ${line}: ${reply}`);
    this.lines.push(`# sim ${line}`);
  }

  whySkip(c: Obj): string | null {
    const variant = (c['variant'] as string | undefined) ?? 'v1';
    if (variant !== (this.preV1 ? 'pre_v1' : 'v1')) return `needs a ${variant} device; this one is ${this.preV1 ? 'pre-v1' : 'v1'}`;
    for (const need of (c['needs'] ?? []) as string[]) {
      if (need.startsWith('fixture:') && !(need.slice(8) in this.fixtures)) return `needs fixture ${need.slice(8)} (--fixture ${need.slice(8)}=PATH)`;
    }
    return null;
  }

  async runCase(c: Obj): Promise<[string, string]> {
    this.lines = [];
    const why = this.whySkip(c);
    if (why) return ['SKIP', why];
    this.ctx = new Context(this.data, this.fixtures, false);
    this.ctx.helloText = this.helloText;
    const given = (c['given'] ?? {}) as Obj;
    const faults = (given['faults'] ?? {}) as Obj;
    let link: CaseLink | null = null;
    try {
      link = await this.open();
      if (given['boot'] === 'fresh') {
        await this.expectOk(link, 'device.reboot', v1.encodeDeviceRebootRequest({ target: 2 }));
        await this.disconnect(link);
        link = await this.open(given['hello'] !== false);
      }
      for (const [name, value] of Object.entries(faults)) await this.control(`set ${name} ${String(value)}`);
      for (const f of (given['files'] ?? []) as Obj[]) {
        await this.write(link, resolve(f['path'], 'str', this.ctx) as string, resolve(f['data'], 'bytes', this.ctx) as Uint8Array);
      }
      const steps = c['steps'] as Obj[];
      for (let n = 0; n < steps.length; n++) {
        this.lines.push(`# step ${n + 1}`);
        try {
          link = (await this.step(link, steps[n]!)) ?? link;
        } catch (error) {
          if (error instanceof Fail) throw new Fail(`step ${n + 1}: ${error.message}`);
          throw error;
        }
      }
      return ['PASS', `${steps.length} steps`];
    } catch (error) {
      if (error instanceof Skip) return ['SKIP', error.message];
      if (error instanceof Fail) return ['FAIL', error.message];
      throw error;
    } finally {
      await link?.close();
      for (const name of Object.keys(faults)) await this.control(`set ${name} ${name === 'space' ? -1 : 0}`).catch(() => undefined);
    }
  }

  async disconnect(link: CaseLink): Promise<void> {
    if (!(await link.gone(this.timing['gone_ms'] as number))) throw new Fail(`the connection did not drop within ${String(this.timing['gone_ms'])} ms`);
    await link.close();
    this.lines.push('# disconnected');
  }

  async expectOk(link: CaseLink, name: v1.CommandName, payload: Uint8Array): Promise<Uint8Array> {
    const reply = await link.request(v1.TYPE[name], payload, Math.min(v1.COMMANDS[name].timeoutMs, this.capMs));
    const status = v1.u32(reply.payload, 0);
    if (status !== 0) throw new Fail(`${name}: ${v1.statusName(status)}, expected ok`);
    return reply.payload;
  }

  async stage(link: CaseLink, body: Uint8Array): Promise<void> {
    for (const r of stageFrames(body)) {
      const name = r.type === v1.TYPE['stage.begin'] ? 'stage.begin' : r.type === v1.TYPE['stage.data'] ? 'stage.data' : 'stage.end';
      const reply = await this.expectOk(link, name, r.payload);
      if (name === 'stage.data') {
        const at = v1.u32(r.payload, 0), received = v1.u32(reply, 4);
        if (received !== at + r.payload.length - 4) throw new Fail(`stage.data at ${at}: received ${received}`);
      }
    }
  }

  async write(link: CaseLink, path: string, body: Uint8Array): Promise<void> {
    await this.stage(link, body);
    const reply = v1.decodeFileWriteStagedReply(await this.expectOk(link, 'file.write_staged',
      v1.encodeFileWriteStagedRequest({ path, sha256: sha256(body) })));
    if (!reply.ok || reply.fields.written !== body.length || reply.fields.readback_crc !== v1.crc32(body)) {
      throw new Fail(`file.write_staged ${path}: not written and read back`);
    }
  }

  async step(link: CaseLink, step: Obj): Promise<CaseLink | null> {
    const action = actionOf(step, 'step');
    if (action === 'send' || action === 'send_type') {
      const r = step['repeat'];
      if (isObj(r)) {
        const from = resolve(r['from'], 'int', this.ctx) as number, to = resolve(r['to'], 'int', this.ctx) as number;
        const by = resolve(r['step'], 'int', this.ctx) as number;
        if (by <= 0) throw new CaseError('repeat step must be above 0');
        for (let value = from; value < to; value += by) {
          this.ctx.vars[String(r['var'])] = value;
          await this.send(link, step);
        }
      } else await this.send(link, step);
    } else if (action === 'each_type') {
      const cmds = BigInt('0x' + helloKeys(this.helloText ?? '')['cmds']);
      for (let type = 0; type < 0x80; type++) {
        if (ALL_TYPES.has(type) || ((cmds >> BigInt(type)) & BigInt(1)) === BigInt(1)) continue;
        const reply = await link.request(type, new Uint8Array(0), this.capMs);
        try {
          this.checkReply(step['expect'] as Obj, null, reply.payload, 0);
        } catch (error) {
          throw new Fail(`type 0x${type.toString(16)}: ${(error as Error).message}`);
        }
      }
    } else if (action === 'send_bytes') {
      await this.sendBytes(link, [resolve(step['send_bytes'], 'bytes', this.ctx) as Uint8Array], false, step['expect'] as Obj);
    } else if (action === 'stream') {
      const st = v1.STREAMS.find((s) => s.name === step['stream'])!;
      await this.sendBytes(link, st.chunks.map((c) => v1.fromHex(c)), true,
        { replies: st.frames.map((f) => ({ type: `0x${(f.type | REPLY).toString(16)}`, seq: f.seq })) });
    } else if (action === 'stage') {
      await this.stage(link, resolve(step['stage'], 'bytes', this.ctx) as Uint8Array);
    } else if (action === 'write') {
      const w = step['write'] as Obj;
      await this.write(link, resolve(w['path'], 'str', this.ctx) as string, resolve(w['data'], 'bytes', this.ctx) as Uint8Array);
    } else if (action === 'disconnect') {
      await this.disconnect(link);
    } else if (action === 'reconnect') {
      await link.close();
      return this.open((step['reconnect'] as Obj)['hello'] !== false);
    } else if (action === 'sim') {
      await link.close();
      await this.control(step['sim'] as string);
    }
    return null;
  }

  async send(link: CaseLink, step: Obj): Promise<void> {
    const r = requestOf(step, this.ctx, 'step');
    const ms = r.command ? Math.min(r.command.timeoutMs, this.capMs) : this.capMs;
    const reply = await link.request(r.type, r.payload, ms, step['seq'] as number | undefined);
    this.checkReply(step['expect'] as Obj, r.command, reply.payload, reply.events);
  }

  async sendBytes(link: CaseLink, chunks: Uint8Array[], separate: boolean, e: Obj): Promise<void> {
    const gap = separate ? 5 : 0;                                         // separate reads on the device side
    if ('none_within_ms' in e) {
      const early = await link.exchange(chunks, gap, e['none_within_ms'] as number, (fs) => fs.length > 0);
      if (early.length) throw new Fail(`expected no frame within ${String(e['none_within_ms'])} ms, got 0x${early[0]!.type.toString(16)} seq ${early[0]!.seq}`);
      return;
    }
    const barrier = 200 + (link.barrier++ % 50);                         // a hello after the bytes
    const isBarrier = (f: v1.Frame): boolean => f.type === (v1.TYPE.hello | REPLY) && f.seq === barrier;
    const got = await link.exchange([...chunks, v1.encodeFrame(v1.TYPE.hello, barrier)], gap, this.capMs, (fs) => fs.some(isBarrier));
    const at = got.findIndex(isBarrier);
    if (at < 0) throw new Fail('no reply to the hello sent after the bytes');
    got.length = at;
    const want = (e['replies'] as Obj[]).map((r) => ({ type: parseInt(r['type'] as string, 16), seq: r['seq'] as number, status: r['status'] as string | undefined }));
    const have = got.map((f) => ({ type: f.type, seq: f.seq, status: f.payload.length >= 4 ? v1.statusName(v1.u32(f.payload, 0)) : '?' }));
    const same = have.length === want.length && have.every((h, i) => h.type === want[i]!.type && h.seq === want[i]!.seq &&
      (want[i]!.status === undefined || want[i]!.status === h.status));
    if (!same) {
      const text = (xs: { type: number; seq: number; status?: string | undefined }[]): string =>
        xs.map((x) => `0x${x.type.toString(16)} seq ${x.seq} ${x.status ?? 'any'}`).join(', ') || 'none';
      throw new Fail(`replies ${text(have)}, expected ${text(want)}`);
    }
  }

  checkReply(e: Obj, command: v1.Command | null, payload: Uint8Array, events: number): void {
    const status = v1.u32(payload, 0);
    const name = v1.statusName(status);
    if ('status' in e) {
      const want = ([] as Json[]).concat(e['status'] ?? []) as string[];
      if (!want.includes(name)) throw new Fail(`${name}, expected ${want.join(' or ')}`);
    }
    if (e['alone'] === true && payload.length !== 4) throw new Fail(`${name} with ${payload.length - 4} bytes after the status, expected the status alone`);
    let values: Record<string, v1.Value> | null = null;
    if (command && (isObj(e['fields']) || isObj(e['save']) || isObj(e['hello']))) {
      try {
        values = v1.decodeReply(command, payload).fields;
      } catch (error) {
        throw new Fail(`reply fields do not decode: ${(error as Error).message}`);
      }
    }
    for (const [field, want] of Object.entries(isObj(e['fields']) ? e['fields'] : {})) {
      if (!values || !(field in values)) throw new Fail(`${name}: the reply has no field ${field}`);
      this.match(values[field]!, want, field);
    }
    for (const [v, field] of Object.entries(isObj(e['save']) ? e['save'] : {})) {
      if (!values || !(String(field) in values)) throw new Fail(`cannot save ${v}: no field ${String(field)}`);
      this.ctx.vars[v] = values[String(field)]!;
    }
    if (command?.name === 'hello' && status === 0) {
      this.helloText = this.ctx.helloText = new TextDecoder().decode(payload.subarray(4));
    }
    if (isObj(e['hello'])) this.checkHello(e['hello'], this.helloText ?? '');
    if (isObj(e['events'])) {
      const min = resolve(((e['events'] as Obj)['log'] as Obj)['min'], 'int', this.ctx) as number;
      if (events < min) throw new Fail(`${events} log events, expected at least ${min}`);
    }
  }

  checkHello(h: Obj, text: string): void {
    const items = text.split(';');
    if ('prefix' in h && items[0] !== h['prefix']) throw new Fail(`hello starts ${String(items[0])}, expected ${String(h['prefix'])}`);
    const keys = helloKeys(items.slice(1).join(';'));
    for (const [key, want] of Object.entries(isObj(h['keys']) ? h['keys'] : {})) {
      if (!(key in keys)) throw new Fail(`hello has no key ${key}`);
      this.match(keys[key]!, want, `hello ${key}`);
    }
    for (const key of (h['lacks'] ?? []) as string[]) if (key in keys) throw new Fail(`hello has key ${key}, expected none`);
    const hello = v1.parseHello(text);
    if ('pre_v1' in h && hello.preV1 !== h['pre_v1']) throw new Fail(`hello is ${hello.preV1 ? 'pre-v1' : 'v1'}`);
    for (const name of (h['answers'] ?? []) as string[]) {
      if (hello.preV1 || !hello.answers(v1.TYPE[name as v1.CommandName])) throw new Fail(`cmds does not list ${name}`);
    }
  }

  match(actual: Val, want: Json, what: string): void {
    const kind: Want = typeof actual === 'number' ? 'int' : typeof actual === 'string' ? 'str' : 'bytes';
    if (isObj(want) && formOf(want) === null) {
      for (const [k, v] of Object.entries(want)) {
        if (k === 'len' || k === 'len_min' || k === 'len_max') {
          const n = (actual as string | Uint8Array).length, bound = resolve(v, 'int', this.ctx) as number;
          if ((k === 'len' && n !== bound) || (k === 'len_min' && n < bound) || (k === 'len_max' && n > bound)) {
            throw new Fail(`${what}: length ${n}, expected ${k} ${bound}`);
          }
        } else if (k === 'min' || k === 'max') {
          const bound = resolve(v, 'int', this.ctx) as number;
          if ((k === 'min' && (actual as number) < bound) || (k === 'max' && (actual as number) > bound)) throw new Fail(`${what}: ${String(actual)}, expected ${k} ${bound}`);
        } else if (k === 'matches') {
          if (!new RegExp(String(v)).test(String(actual))) throw new Fail(`${what}: ${String(actual)} does not match ${String(v)}`);
        } else if (k === 'one_of') {
          if (!(v as Json[]).some((x) => equal(actual, resolve(x, kind, this.ctx)))) throw new Fail(`${what}: ${show(actual)} is not one of ${JSON.stringify(v)}`);
        }
      }
      return;
    }
    const expect = resolve(want, kind, this.ctx, what);
    if (!equal(actual, expect)) throw new Fail(`${what}: ${show(actual)}, expected ${show(expect)}`);
  }
}

// ---- running a file -----------------------------------------------------------------------------

export interface Row { name: string; result: 'PASS' | 'FAIL' | 'SKIP'; seconds: number; detail: string; log: string[] }

/** The cases of a conformance file, checked by interpret() first (throws CaseError on bad data). */
export function loadCases(path: string | URL = new URL('../../../protocol/v1.conformance.json', HERE),
  fixtures: Record<string, string> = {}): Obj {
  const data = JSON.parse(readFileSync(path, 'utf-8')) as Obj;
  if (data['format'] !== 'xyf-conformance/1' || data['protocol'] !== v1.VERSION) throw new CaseError('format or protocol version differs');
  for (const c of data['cases'] as Json[]) interpret(c, data, fixtures);
  return data;
}

/** Runs every case (matching only) against the simulator at host:port; null when it does not answer. */
export async function runCases(data: Obj, host: string, port: number, fixtures: Record<string, string>,
  only: RegExp | null = null, each?: (row: Row) => void): Promise<Row[] | null> {
  const runner = new Runner(data, host, port, fixtures);
  try {
    await (await runner.open(true, 2000)).close();
  } catch {
    return null;
  }
  const rows: Row[] = [];
  for (const c of data['cases'] as Obj[]) {
    if (only && !only.test(String(c['name']))) continue;
    const start = Date.now();
    const [result, detail] = await runner.runCase(c);
    const row = { name: String(c['name']), result: result as Row['result'], seconds: (Date.now() - start) / 1000, detail, log: runner.lines };
    rows.push(row);
    each?.(row);
  }
  return rows;
}

// ---- command line -------------------------------------------------------------------------------

async function main(argv: string[]): Promise<number> {
  const opts: Record<string, string> = {};
  const fixtures: Record<string, string> = {};
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]!;
    if (a === '--check') opts['check'] = '1';
    else if (['--sim', '--only', '--cases', '--fixture'].includes(a) && i + 1 < argv.length) {
      const value = argv[++i]!;
      if (a === '--fixture') {
        const [name, path] = [value.slice(0, value.indexOf('=')), value.slice(value.indexOf('=') + 1)];
        if (!FIXTURES.includes(name)) { console.error(`conformance: --fixture ${value}: NAME is one of ${FIXTURES}`); return 2; }
        fixtures[name] = path;
      } else opts[a.slice(2)] = value;
    } else { console.error(`conformance: unknown argument ${a}`); return 2; }
  }
  let data: Obj;
  try {
    data = loadCases(opts['cases'], fixtures);
  } catch (error) {
    console.error(`conformance: ${(error as Error).message}`);
    return 2;
  }
  if (opts['check']) {
    console.log(`ok ${(data['cases'] as Json[]).length} cases interpreted`);
    return 0;
  }
  const m = /^(127\.0\.0\.1|localhost|::1):([0-9]+)$/.exec(opts['sim'] ?? '');
  if (!m) { console.error('conformance: --sim 127.0.0.1:PORT (the simulator; this runner has no USB transport)'); return 2; }
  const rows = await runCases(data, m[1]!, parseInt(m[2]!, 10), fixtures, opts['only'] ? new RegExp(opts['only']) : null, (row) => {
    if (row.result === 'FAIL' && process.env['XYF_CONFORMANCE_LOG']) console.error(row.log.join('\n'));
  });
  if (rows === null) {
    console.log(`NEED: start the simulator (python3 -m xy_forever.sim), then rerun with --sim ${opts['sim']}`);
    return 3;
  }
  const width = Math.max(4, ...rows.map((r) => r.name.length));
  console.log(`${'case'.padEnd(width)}  result seconds  detail`);
  for (const r of rows) console.log(`${r.name.padEnd(width)}  ${r.result.padEnd(6)} ${r.seconds.toFixed(1).padStart(7)}  ${r.detail}`);
  return rows.some((r) => r.result === 'FAIL') ? 1 : 0;
}

if (process.argv[1] !== undefined && import.meta.url === pathToFileURL(process.argv[1]).href) {
  process.exitCode = await main(process.argv.slice(2));
}
