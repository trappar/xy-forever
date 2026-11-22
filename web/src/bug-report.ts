// This module owns the export schema. Project the normal hello status before keeping it,
// and keep only fixed activity names and codes. The preview never needs a device request.
import { JobError, STATUS, isXyfError } from 'xy-forever-client';
import type { DeviceInfo } from 'xy-forever-client';
import appPackage from '../package.json';

const ACTIONS = [
  'connect',
  'disconnect',
  'refresh_status',
  'update_xy_forever',
  'uninstall_xy_forever',
  'list_packages',
  'choose_packages',
  'send_packages',
  'remove_packages',
  'save_mods',
  'browse_files',
  'download_file',
  'upload_files',
  'new_folder',
  'copy_file',
  'move_file',
  'delete_file',
] as const;
export type Action = (typeof ACTIONS)[number];
const OUTCOMES = ['finished', 'failed'] as const;
type Outcome = (typeof OUTCOMES)[number];
const ACTIVITY_LIMIT = 24;
const ERROR_CODES = new Set([
  ...Object.keys(STATUS).filter((code) => code !== 'ok'),
  'unknown_status',
  'timeout',
  'disconnected',
  'aborted',
  'update_required',
  'protocol',
]);

// The protocol specifies numeric releases and protocol versions. Bound each component so
// strings with arbitrarily long numbers cannot pass for a version.
const RELEASE = /^(0|[1-9][0-9]{0,4})(\.(0|[1-9][0-9]{0,4})){2}$/;
const PROTOCOL = /^(0|[1-9][0-9]{0,4})\.(0|[1-9][0-9]{0,4})$/;

type HelloStatus = Pick<DeviceInfo, 'version' | 'proto' | 'image' | 'mode' | 'dev'>;
type DeviceStatus = {
  firmwareVersion: string | null;
  protocolVersion: string | null;
  image: string | null;
  mode: 'product' | null;
  developerBuild: boolean | null;
};
type Activity = { event: Action; outcome: Outcome; code?: string };

function matchingText(value: unknown, pattern: RegExp): string | null {
  return typeof value === 'string' && pattern.test(value) ? value : null;
}

function errorCode(error: unknown): string {
  if (isXyfError(error) && ERROR_CODES.has(error.code)) return error.code;
  if (error instanceof JobError) return error.status.phase === 'cancelled' ? 'job_cancelled' : 'job_failed';
  return 'app_error';
}

export class BugReport {
  #device: DeviceStatus | null = null;
  #activity: Activity[] = [];

  updateStatus(status: HelloStatus): void {
    this.#device = {
      firmwareVersion: matchingText(status.version, RELEASE),
      protocolVersion: matchingText(status.proto, PROTOCOL),
      image: matchingText(status.image, /^[0-9a-f]{8}$/),
      mode: status.mode === 'product' ? 'product' : null,
      developerBuild: typeof status.dev === 'boolean' ? status.dev : null,
    };
  }

  record(event: Action, outcome: Outcome, error?: unknown): void {
    if (!ACTIONS.includes(event) || !OUTCOMES.includes(outcome)) return;
    this.#activity.push(outcome === 'failed' ? { event, outcome, code: errorCode(error) } : { event, outcome });
    if (this.#activity.length > ACTIVITY_LIMIT) this.#activity.shift();
  }

  snapshot(connected: boolean): string {
    return (
      JSON.stringify(
        {
          bundleVersion: 1,
          appVersion: matchingText(appPackage.version, RELEASE),
          connected: connected === true,
          device: this.#device,
          activity: this.#activity,
        },
        null,
        2,
      ) + '\n'
    );
  }
}

/** The bug report's activity and last known device, for this tab. */
export const bugReport = new BugReport();
