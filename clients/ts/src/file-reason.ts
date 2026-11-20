// A file command's device error in plain words (split out of client.ts): the status, the command and the file
// system's error number.
import type { XyfError } from './errors.ts';
import { PROTECTED_NAMES } from './write-area.ts';
import * as v1 from './v1.ts';

const STATUSES_BY_NAME = new Map<string, string>(v1.STATUSES.map((s) => [s.name, s.doc]));
const CHANGES = new Set(['file.write_staged', 'file.delete', 'file.rmdir', 'file.mkdir', 'file.rename']);
const FS_ERRORS: Record<number, string> = { 2: 'no such file or folder', 17: 'the name exists already', 20: 'not a folder',
  21: 'a folder, not a file', 22: 'a folder cannot move into itself', 93: 'the folder is not empty' };

/** A file command's device error in plain words (the status and the file system's error number). */
export function fileReason(error: XyfError): string {
  const reason = plainReason(error);
  return error.command === 'file.copy' && error.fields?.['partial'] === 1 ? `${reason}; a partial target may remain` : reason;
}

function plainReason(error: XyfError): string {
  const change = error.command !== null && CHANGES.has(error.command);
  const errno = typeof error.fields?.['error'] === 'number' ? error.fields['error'] : 0;
  const roots = `/yaffs2/, never ${PROTECTED_NAMES.join(' or ')} (or inside it)`;
  switch (error.code) {
    case 'refused':
      return change ? `the OP-XY changes files only under ${roots}, at most 6 folders deep`
        : error.command === 'file.copy' ? `the OP-XY copies only a file it may read, to a path under ${roots}`
          : 'the OP-XY refuses this path (outside /yaffs2, an empty, "." or ".." part, or a control character)';
    case 'not_found':
      return error.command === 'file.mkdir' ? 'the parent folder does not exist'
        : error.command === 'file.list' || error.command === 'file.rmdir' ? 'no such folder'
          : error.command === 'file.rename' ? "no such file or folder, or the target's folder does not exist"
            : error.command === 'file.copy' ? "no such file, or the target's folder does not exist" : 'no such file';
    case 'state':
      return error.command === 'file.rmdir' ? 'the folder is not empty: remove what it holds first'
        : error.command === 'file.rename' || error.command === 'file.copy' ? 'the target exists already (nothing replaced)'
          : error.command === 'file.write_staged' ? 'nothing complete is staged' : 'the device was not ready (send hello first)';
    case 'storage':
      return FS_ERRORS[errno] ?? `the device's file system failed${errno ? ` (error ${errno})` : ''}`;
    case 'bad_request':
      return 'a malformed path (too long, or an empty, "." or ".." part)';
    case 'link':
      return 'processor A did not answer; try again, or restart the OP-XY';
    case 'busy':
      return 'the OP-XY is building mod firmware (a job is running); try again when it ends, or cancel it';
    case 'crc':
      return error.command === 'file.copy' ? 'the copy read back differs from the source' : 'the file read back differs from what was sent';
    case 'too_large':
      return error.command === 'file.copy' ? 'the file is larger than the OP-XY copies at a time (hello stage=)'
        : STATUSES_BY_NAME.get(error.code) ?? 'too large';
    default:
      return STATUSES_BY_NAME.get(error.code) ?? `the device answered ${error.code}`;
  }
}
