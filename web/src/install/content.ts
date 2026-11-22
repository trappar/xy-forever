// The Install page's words for each stage: Open-XY's builder flow (builder/wizard.mjs, staging ca122f4; MIT, see
// ../vendor/open-xy/LICENSE), adapted where XY Forever differs (flow.ts says how).
import { CHOOSE_TEXT } from 'xy-forever-client';
import { STOCK_KB, STOCK_PATH } from './checks.ts';
import type { Action, InstallDeps, View } from './flow.ts';
import type { ReleasePin } from './release.ts';
import type { InstallStage } from './stages.ts';

/** [title, detail, actions, instructions] */
export type Stage = [title: string, detail: string, actions: Action[], instructions?: string[]];
export const action = (label: string, next: InstallStage | null, id?: string): Action =>
  id ? { label, next, id } : { label, next };

/** Open-XY's stage table, adapted: [title, detail, actions, instructions]. */
export function stageContent(v: View, deps: Pick<InstallDeps, 'serial'>, release: ReleasePin | null): Stage {
  const { busy, computer, delivered, log, stop } = v;
  const sendsDirectly = (): boolean => v.sendsDirectly;
  const transferSteps = [
    'Open your OP–XY in File Explorer.',
    'Select Download os.ldr below.',
    'Drag os.ldr to your OP–XY’s root folder.',
    'Confirm when asked to replace the file.',
    `Check that os.ldr shows about ${release ? Math.ceil(release.bytes / 1024) : '?'} KB, not 0 KB.`,
  ];
  const official = [
    'Switch off. Hold COM while switching on.',
    'Press track 1: “upload firmware”, then connect USB.',
    'Copy the downloaded 1.2.1 .tfw to the removable disk, then eject it.',
    'Wait for the update to finish, then select Start again.',
  ];
  const stages: Record<InstallStage, Stage> = {
    boot: [
      'Start in the te-boot menu.',
      'The page first checks that your OP–XY can take XY Forever. It changes nothing until then.',
      [action(busy ? 'Checking…' : 'Connected and at te-boot · Check my OP–XY', null, 'te-check')],
      [
        'Power off your OP–XY.',
        'Hold COM while switching it on.',
        'Release COM when the te-boot menu appears.',
        'Connect it to this computer with USB.',
        'Allow MIDI access if asked.',
      ],
    ],
    menu: [
      'Does your screen show "3. dev menu"?',
      'Check the te-boot menu on your OP–XY.',
      [action('No, it’s missing', 'enable', 'menu-no'), action('Yes, I see it', 'computer', 'menu-yes')],
    ],
    enable: [
      'Enable the dev menu.',
      'Keep your OP–XY at te-boot.',
      [action(busy ? 'Enabling…' : 'Enable dev menu', null, 'dev-enable')],
      [
        'Select Enable dev menu below. Allow MIDI access if asked.',
        'XY Forever leaves the dev menu on: it is how your OP–XY can always be recovered.',
      ],
    ],
    'refresh-open': [
      'Open the system menu.',
      'On your OP–XY, press track 8 for "8. system menu".',
      [action('System menu is open', 'refresh-return')],
    ],
    'refresh-return': [
      'Return to te-boot.',
      'Press track 8 for "8. back" to refresh the te-boot menu.',
      [action('I’m back at te-boot', 'confirm')],
    ],
    confirm: [
      'Does your screen show "3. dev menu"?',
      'Check the te-boot menu on your OP–XY.',
      [action('Yes, I see it', 'computer', 'dev-confirm'), action('No, it’s still missing', 'menu-help')],
    ],
    'menu-help': [
      'The dev menu is still missing.',
      'Keep your OP–XY at te-boot and retry enabling it, or use Connection help below.',
      [
        action(busy ? 'Enabling…' : 'Retry enabling dev menu', null, 'dev-enable'),
        action('Refresh the screen again', 'refresh-open'),
      ],
    ],
    computer: [
      'Which computer are you using?',
      'The device has a transfer option for each system.',
      [action('Windows', 'open-dev-menu', 'computer-windows'), action('Mac', 'open-dev-menu', 'computer-mac')],
    ],
    'open-dev-menu': [
      'Open the dev menu.',
      'Press track 3 for "3. dev menu". Your OP–XY calls this screen "factory menu".',
      [action('Dev menu is open', 'start-transfer')],
    ],
    'start-transfer': [
      'Start file transfer.',
      computer === 'mac'
        ? 'Press track 2 for "2. mtp file transfer (mac)".'
        : 'Press track 3 for "3. mtp file transfer (windows)".',
      [action('I see "mtp mode active"', sendsDirectly() ? 'close-field-kit' : 'check-stock')],
    ],
    'check-stock': [
      'Check the official OS file.',
      `In File Explorer, open your OP–XY, then ${STOCK_PATH.split('/').slice(0, -1).join(' › ')}. os.ldr must be there, about ${STOCK_KB}: XY Forever adds that file to its library at its first start. Change nothing there.`,
      [
        action(`I see os.ldr, about ${STOCK_KB}`, 'transfer', 'stock-seen'),
        action('It’s missing or another size', null, 'stock-missing'),
      ],
    ],
    'close-field-kit': [
      'Quit Field Kit and OpenMTP.',
      'These apps can hold your OP–XY’s USB connection. Quit Field Kit, OpenMTP and Android File Transfer if any are running, then continue.',
      [action('They’re closed · Continue', 'transfer', 'field-kit-closed')],
    ],
    transfer: sendsDirectly()
      ? [
          'Send XY Forever to your OP–XY.',
          'The page checks that the original OS file is there and the free storage, copies os.ldr to the root and reads it back. Your device’s other content stays in place.',
          delivered
            ? [action('File sent · Continue', 'exit-transfer')]
            : [action(busy ? 'Sending…' : log.length ? 'Try again' : 'Send to OP–XY', null, 'mtp-send')],
        ]
      : [
          'Copy the OS file.',
          'Close any File Explorer windows showing your OP–XY’s other folders. Keep your device’s other content in place.',
          [action('Download os.ldr', null, 'download'), action('File copied · Continue', 'exit-transfer')],
          transferSteps,
        ],
    'exit-transfer': [
      'Exit file transfer.',
      'Once the file has finished copying, press track 2 for "2. exit" to return to the dev menu (titled "factory menu" on your OP–XY).',
      [action('I’m back at the dev menu', 'write-os')],
    ],
    'write-os': [
      'Write the OS.',
      'Press track 5 for "5. write os to boot partition". Keep your OP–XY connected to power.',
      [action('I pressed track 5', 'write-result')],
    ],
    'write-result': [
      'Wait for the write result.',
      'Don’t power off while writing. When "firmware written ok!" appears under "looking for /os.ldr", it’s safe to power cycle your OP–XY.',
      [action('I see "firmware written ok!"', 'start'), action('I see something else', 'write-help')],
    ],
    'write-help': [
      'Check the message on your OP–XY.',
      'If it stops at "looking for /os.ldr" without "firmware written ok!", os.ldr isn’t at the device’s top level. For any other message, press track 5 again and wait; don’t power off until "firmware written ok!" appears.',
      [action('Return to the write status', 'write-result')],
    ],
    start: [
      'Start XY Forever.',
      'Switch your OP–XY off, then on.',
      deps.serial ? [action(busy ? 'Looking…' : 'Find my OP–XY', null, 'serial-find')] : [],
      [
        'On this first start XY Forever sets itself up: it adds your OP–XY’s own OS 1.2.1 to its library ("Adding OS 1.2.1"), makes it the default, then starts it. Expect the logo to stay up longer than usual, up to about 20 seconds.',
        'To manage mods: switch off, then hold PLAY while switching on. That opens the XY Forever menu, and this page takes you to your mods.',
        ...(deps.serial
          ? [
              'A page that connected before finds your OP–XY by itself; otherwise select Find my OP–XY. ' + CHOOSE_TEXT,
              'If your OP–XY asks to allow this computer, press PLAY.',
            ]
          : ['This browser has no Web Serial: open this page in Chrome or Edge to continue to your mods.']),
      ],
    ],
    'mtp-help': [
      'Start fresh with OS 1.2.1.',
      'Format the drive, reinstall official OS 1.2.1, then continue installing XY Forever.',
      [action('I understand the data loss · Show format steps', 'format-drive', 'format-start')],
      [
        'Formatting erases all projects, samples, presets and soundpacks. Back up your files and check the copies.',
        'Can’t back up files you need? Stop here.',
        'Download OS 1.2.1 below. Keep your soundpack installers for later.',
      ],
    ],
    'format-drive': [
      'Format the drive on your OP–XY.',
      'This erases your data. Keep the OP–XY on power.',
      [action('Format finished · Reinstall OS 1.2.1', 'format-upload')],
      [
        'In te-boot, press track 5: “format drive”.',
        'Check that you see “format drive”, not “write os to boot partition”.',
        'Follow the device’s prompts. If the menu differs, stop.',
        'Wait until formatting finishes before continuing.',
      ],
    ],
    'format-upload': [
      'Reinstall official OS 1.2.1.',
      'Use the official .tfw download below.',
      [action('1.2.1 reinstalled · Continue', null, 'recovery-retry')],
      [
        'Once formatting finishes, switch off. Hold COM while switching on.',
        'Press track 1: “upload firmware”, then connect USB.',
        'Copy the .tfw file to the removable disk and eject it.',
        'Wait for the update to finish before continuing.',
      ],
    ],
    stop: [
      stop?.title ?? '',
      stop?.detail ?? '',
      [action('Start again', null, 'flow-restart')],
      stop?.kind === 'update' || stop?.kind === 'reinstall' ? official : [],
    ],
  };
  return stages[v.stage];
}
