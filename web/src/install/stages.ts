// The Install page's stages, in flow order: each is a path, /install/<stage>.
export const INSTALL_STAGES = [
  'boot', 'menu', 'enable', 'refresh-open', 'refresh-return', 'confirm', 'menu-help', 'computer',
  'open-dev-menu', 'start-transfer', 'check-stock', 'close-field-kit', 'transfer', 'exit-transfer',
  'write-os', 'write-result', 'write-help', 'start', 'mtp-help', 'format-drive', 'format-upload', 'stop',
] as const;
export type InstallStage = (typeof INSTALL_STAGES)[number];
export const isInstallStage = (value: unknown): value is InstallStage => INSTALL_STAGES.includes(value as InstallStage);
