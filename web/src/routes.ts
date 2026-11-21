// These are both the app's routes and the directories emitted by the static build.
export const PAGES_BASE = '/xy-forever/';
export const PAGES = ['connect', 'install', 'device', 'mods', 'files'] as const;
export type Page = (typeof PAGES)[number];

export const INSTALL_STAGES = [
  'boot', 'menu', 'enable', 'refresh-open', 'refresh-return', 'confirm', 'menu-help', 'computer',
  'open-dev-menu', 'start-transfer', 'check-stock', 'close-field-kit', 'transfer', 'exit-transfer',
  'write-os', 'write-result', 'write-help', 'start', 'mtp-help', 'format-drive', 'format-upload', 'stop',
] as const;
export type InstallStage = (typeof INSTALL_STAGES)[number];
