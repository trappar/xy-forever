// The Install page (#178): the flow (install/flow.ts) in one instance kept across its stages and pages, its words
// (install/content.ts), and the app's side of it: the connection it may lend, find or open, and the switch to Manage
// (detect.ts).
import { useEffect } from 'react';
import type { ReactNode } from 'react';
import { useParams } from '@tanstack/react-router';
import { useStore } from 'zustand';
import { RELEASE_FILTERS } from 'xy-forever-client';
import type { Client } from 'xy-forever-client';
import { Button } from '@/components/ui/button.tsx';
import { Card, CardTitle } from '@/components/ui/card.tsx';
import { Progress } from '@/components/ui/progress.tsx';
import { ChannelChoice, ReleaseIdentity } from '@/components/channel-choice.tsx';
import { PageHeader } from '@/components/page-header.tsx';
import { cn } from '@/lib/utils.ts';
import { useConnection } from '../connection.ts';
import { findDevice, toManage } from '../detect.ts';
import { canConnect, openDevice } from '../device.ts';
import { OFFICIAL_121_TFW, OFFICIAL_UPDATE_GUIDE } from '../install/checks.ts';
import { action, stageContent } from '../install/content.ts';
import { NO_BACK, installFlow } from '../install/flow.ts';
import type { InstallDeps, InstallFlow, Lease } from '../install/flow.ts';
import { loadPath } from '../routing.ts';

/** This app's connection, lent (close keeps it): while it holds the port, the Install page uses it and never opens a
 * second link (two readers on one OP-XY split its replies). */
function own(): Lease | null {
  const { client } = useConnection.getState();
  return client?.connected ? { client, close: async () => {} } : null;
}
const lease = (found: Client): Lease => ({ client: found, close: () => found.close() });

function appDeps(): InstallDeps {
  const nav = navigator as Navigator & {
    requestMIDIAccess?: (o: { sysex: boolean }) => Promise<unknown>;
    usb?: unknown;
    serial?: { addEventListener?: (type: string, listener: () => void) => void };
  };
  return {
    midi: nav.requestMIDIAccess ? nav.requestMIDIAccess.bind(nav) : null,
    usb: nav.usb ?? null,
    serial: canConnect(),
    connect: async () => own() ?? lease(await openDevice({ filters: RELEASE_FILTERS })), // a click: the chooser when needed
    find: async () => {
      // never the chooser: this app's connection, else a granted port that answers (or the simulator)
      const mine = own();
      if (mine) return mine;
      const found = await findDevice(RELEASE_FILTERS);
      return found && lease(found);
    },
    handoff: (found, installed, cleaned) => toManage(found.client, installed, cleaned),
    onPort: (listener) => nav.serial?.addEventListener?.('connect', listener),
  };
}

let flow: InstallFlow | null = null;
let shownBefore = false;
const theFlow = (): InstallFlow => (flow ??= installFlow(appDeps()));
// Uninstalled: the install flow starts over.
useConnection.subscribe((now, before) => {
  if (now.stockRestored && !before.stockRestored) flow?.reset();
});

export default function InstallPage() {
  const { stage: path } = useParams({ strict: false });
  const f = theFlow();
  useEffect(() => {
    f.setShown(true);
    return () => f.setShown(false);
  }, [f]);
  useEffect(() => {
    // the first page this document shows counts as the load (a reload asks te-boot again)
    const how = shownBefore || !loadPath.includes('/install/') ? 'push' : 'load';
    shownBefore = true;
    f.setShown(true);
    f.show(`/install/${path ?? ''}`, how);
  }, [f, path]);
  return <InstallView flow={f} />;
}

const link = (href: string, text: string): ReactNode => (
  <a href={href} target="_blank" rel="noopener noreferrer" className="text-primary underline">
    {text}
  </a>
);
const Reminder = ({ title, children }: { title: string; children: ReactNode }) => (
  <aside className="border-l-3 border-primary px-3 py-1">
    <h3 className="my-1 text-sm font-semibold">{title}</h3>
    <p>{children}</p>
  </aside>
);

/** Open-XY's MTP troubleshooting FAQ, adapted to one file and to XY Forever's recovery (the dev menu stays on). */
const Faq = ({ open }: { open: boolean }) => (
  <details
    data-testid="mtp-faq"
    open={open}
    className="text-muted-foreground [&_h3]:mt-3 [&_h3]:mb-0.5 [&_h3]:text-sm [&_h3]:font-semibold [&_h3]:text-foreground"
  >
    <summary className="cursor-pointer">MTP troubleshooting FAQ</summary>
    <h3>Did installing soundpacks cause this?</h3>
    <p>
      We don’t know. Persistent failures were reported with soundpacks installed, but that does not establish a cause.
      Removing soundpacks has not been verified as a fix.
    </p>
    <h3>The copy keeps failing. What next?</h3>
    <p>
      Close Field Kit, OpenMTP, Android File Transfer and any File Explorer windows showing your OP–XY, then try again.
      If it still fails, the fresh-install guide walks you through formatting the drive and reinstalling official OS
      1.2.1 before installing XY Forever. Back up your files first: formatting erases the drive. This resolved a
      reported MTP problem, but is not a guaranteed fix.
    </p>
    <h3>MTP works, but the original OS file is missing.</h3>
    <p>
      This is a separate problem: the page then asks you to reinstall official OS 1.2.1, without formatting the drive.
    </p>
    <h3>Where does the file go?</h3>
    <p>
      Copy <code>os.ldr</code> to the root, not into <code>upgrade/firmware/blackfin/bin</code>. After copying, follow
      the write-OS steps; copying alone does not install firmware.
    </p>
  </details>
);

function InstallView({ flow: f }: { flow: InstallFlow }) {
  const v = useStore(f.view);
  const { stage, busy, stop, log, status, fraction } = v;
  const { release, canary } = v;
  const { deps } = f;
  const [title, detail, actions, instructions = []] = stageContent(v, deps, release);
  const official =
    stage === 'mtp-help' ||
    stage === 'format-upload' ||
    (stage === 'stop' && (stop?.kind === 'update' || stop?.kind === 'reinstall'));
  const missing = [!deps.midi && 'Web MIDI', !deps.serial && 'Web Serial'].filter(Boolean);
  const startable = stage !== 'boot' || (release !== null && deps.midi !== null);
  return (
    <section id="page-install" data-stage={stage} aria-busy={busy}>
      <PageHeader title="Install XY Forever">
        {stage === 'boot' &&
          'For an OP–XY with teenage engineering’s OS 1.2.1. You need this page in Chrome or Edge on a Mac or Windows computer, and a USB-C data cable: no other apps.'}
      </PageHeader>
      {stage === 'boot' && (
        <p data-testid="already-installed" className="mb-4 max-w-[62ch] rounded-lg border bg-card px-3.5 py-2.5">
          <strong>Already have XY Forever?</strong> Switch your OP–XY on while holding PLAY and connect it by USB: this
          site switches to Manage by itself.
          {deps.serial && release && ' The first time on this computer, select Find my OP–XY below and choose it.'}
        </p>
      )}
      {!release && (
        <Card className="border-destructive/60" data-refusal="no-release">
          <CardTitle>No XY Forever release here yet</CardTitle>
          <p>This copy of the web app has no XY Forever release to install.</p>
        </Card>
      )}
      {stage === 'boot' && missing.length > 0 && (
        <Card className="border-destructive/60" data-refusal="browser">
          <p>
            This browser lacks {missing.join(' and ')}. Open this page in Chrome or Edge on a Mac or Windows computer.
          </p>
        </Card>
      )}
      <Card
        className={cn(stage === 'stop' && 'border-destructive/60')}
        data-verdict={stage === 'stop' ? stop?.kind : undefined}
      >
        {v.formatting && <p className="text-muted-foreground">Start fresh with OS 1.2.1</p>}
        <CardTitle id="flow-title">{title}</CardTitle>
        {detail && <p>{detail}</p>}
        {stage === 'boot' && f.stable && canary && (
          <ChannelChoice
            stable={f.stable}
            canary={canary}
            value={release?.sha256 === canary.sha256 ? 'canary' : 'stable'}
            onChange={f.choose}
            disabled={busy}
          />
        )}
        {release && ['boot', 'transfer'].includes(stage) && <ReleaseIdentity release={release} />}
        {instructions.length > 0 && (
          <ol className="max-w-[62ch] list-decimal space-y-1 pl-[22px]">
            {instructions.map((t) => (
              <li key={t}>{t}</li>
            ))}
          </ol>
        )}
        {official && (
          <p data-official="1.2.1">
            {link(OFFICIAL_121_TFW, 'Download official OS 1.2.1 (.tfw)')} ·{' '}
            {link(OFFICIAL_UPDATE_GUIDE, 'Official installation guide')}
            <br />
            <span className="text-warn">
              Use exactly this 1.2.1 file. Never install 1.2.5, the newest on teenage engineering’s site: it cannot be
              undone, and XY Forever cannot run on it.
            </span>
          </p>
        )}
        {stage === 'transfer' && !v.sendsDirectly && (
          <Reminder title="Yes, copy to the ROOT folder.">
            Copy os.ldr to the top level of your OP–XY. Do not put it in <code>upgrade/firmware/blackfin/bin</code>.{' '}
            <strong>Replace the existing file if prompted.</strong>
          </Reminder>
        )}
        {['transfer', 'exit-transfer', 'write-os'].includes(stage) && (
          <Reminder title="You are not done yet!">
            Copying the file does not finish installation. Continue through the next steps to install the OS before
            restarting.
          </Reminder>
        )}
        {log.length > 0 && (
          <ol aria-label="Transfer log" className="list-decimal pl-[22px] font-mono text-[13px] text-muted-foreground">
            {log.map((t, i) => (
              <li key={i}>{t}</li>
            ))}
          </ol>
        )}
        {fraction !== null && <Progress value={fraction * 100} />}
        {startable && (
          <div className="flex flex-wrap items-center gap-2.5">
            {actions.map((a, i) => (
              <Button
                key={a.label}
                id={a.id}
                variant={i === 0 ? 'default' : 'outline'}
                disabled={busy}
                onClick={() => f.click(a)}
              >
                {a.label}
              </Button>
            ))}
          </div>
        )}
        {['start-transfer', 'close-field-kit', 'check-stock', 'transfer'].includes(stage) && (
          <div>
            <Button disabled={busy} onClick={() => f.advance('mtp-help')}>
              I’m having trouble connecting to MTP
            </Button>
          </div>
        )}
        {['mtp-help', 'transfer'].includes(stage) && <Faq open={stage === 'mtp-help'} />}
        {status && (
          <p role="status" className={v.error ? 'text-[#ffb4b4]' : 'text-muted-foreground'}>
            {status}
          </p>
        )}
        {['boot', 'enable', 'menu-help'].includes(stage) && (
          <details data-testid="connection-help" className="text-muted-foreground">
            <summary className="cursor-pointer">Connection help</summary>
            <p>
              Use a compatible desktop browser, such as Chrome or Edge. Close other MIDI apps. If this page cannot get
              MIDI permission, allow MIDI for this site in the browser’s site settings, reload the page, then check your
              OP–XY again.
            </p>
            <p>{link('https://github.com/bagofwaffles/ferret', 'Ferret by Bag of Waffles')} · MIT license</p>
          </details>
        )}
        {deps.serial && release && stage !== 'start' && (
          <p className="text-muted-foreground">
            Already running XY Forever?{' '}
            <Button
              id="serial-find"
              size="sm"
              disabled={busy}
              onClick={() => f.click(action('Find my OP–XY', null, 'serial-find'))}
            >
              Find my OP–XY
            </Button>
          </p>
        )}
        {v.depth > 0 && !NO_BACK.includes(stage) && (
          <div>
            <Button disabled={busy} onClick={f.back}>
              ← Back
            </Button>
          </div>
        )}
      </Card>
    </section>
  );
}
