// Typed errors of the call layer: one class, told apart by `code`. A device status keeps its spec
// name (`refused`, `busy`, `link`, ...); the others come from the client side.
import { STATUS, statusName } from './v1.ts';
import type { CommandName, StatusName, Value } from './v1.ts';

export type ErrorCode =
  | Exclude<StatusName, 'ok'>   // the device answered with this status
  | 'unknown_status'            // a status this client does not know (a newer device): a failure
  | 'timeout'                   // no reply within the call's timeout
  | 'disconnected'              // the link closed: cable pulled, device restarted, port closed
  | 'aborted'                   // the caller cancelled (AbortSignal)
  | 'update_required'           // the device's XY Forever is too old for this client or command
  | 'protocol';                 // a reply this client cannot read, or another protocol major

export class XyfError extends Error {
  readonly code: ErrorCode;
  readonly command: CommandName | null;
  /** The device's status code when the device answered with an error, else null. */
  readonly status: number | null;
  /** The fields an error reply carries (the command's `on_error`), else null. */
  readonly fields: Readonly<Record<string, Value>> | null;

  constructor(code: ErrorCode, message: string, more: {
    command?: CommandName | null; status?: number | null; fields?: Readonly<Record<string, Value>> | null; cause?: unknown;
  } = {}) {
    super(message, more.cause === undefined ? undefined : { cause: more.cause });
    this.name = 'XyfError';
    this.code = code;
    this.command = more.command ?? null;
    this.status = more.status ?? null;
    this.fields = more.fields ?? null;
  }
}

const KNOWN = new Set<number>(Object.values(STATUS));

/** The error for a reply whose status is not ok. */
export function statusError(command: CommandName | null, status: number, fields: Readonly<Record<string, Value>> | null = null): XyfError {
  const name = statusName(status);
  const code: ErrorCode = KNOWN.has(status) ? (name as Exclude<StatusName, 'ok'>) : 'unknown_status';
  return new XyfError(code, `${command ?? 'request'}: the device answered ${name}`, { command, status, fields });
}

/** True when error is an XyfError (with one of the codes, when given). */
export function isXyfError(error: unknown, ...codes: ErrorCode[]): error is XyfError {
  return error instanceof XyfError && (codes.length === 0 || codes.includes(error.code));
}
