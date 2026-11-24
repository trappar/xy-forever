// Device: build, what is running, what boots next, the saved mods, the content state; Update when a newer pinned
// release exists, or the site's canary (#301: the owner's channel choice, kept in this browser), and Uninstall
// (restores stock OS 1.2.1, then the app goes to Install).
import { useEffect, useState } from 'react';
import type { ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { DeviceInfo } from 'xy-forever-client';
import { Button } from '@/components/ui/button.tsx';
import { Card, CardTitle } from '@/components/ui/card.tsx';
import { PageHeader } from '@/components/page-header.tsx';
import { ChannelChoice, ReleaseIdentity, useCanary } from '@/components/channel-choice.tsx';
import { confirmAction } from '../ask.tsx';
import { bugReport } from '../bug-report.ts';
import { useConnection } from '../connection.ts';
import { device, queryClient, readText, savedConfig, useAction } from '../device.ts';
import { channelOf, pinned, releaseName, validPin } from '../install/release.ts';
import type { Channel, ReleasePin } from '../install/release.ts';
import { CONTENT_STATE, CONTENT_STATE_OLD, configText, contentText, modeText } from '../model.ts';
import { navigatePath } from '../routing.ts';
import { UNINSTALL_PROTOCOL, uninstallAvailable, uninstallXyf } from '../uninstall.ts';
import { updateAvailable, updateXyf } from '../update.ts';

async function loadDevice() {
  const c = device();
  const info = await c.status();
  bugReport.updateStatus(info);
  const state = await c.readState();
  let boots = 'No default set: the menu decides at power-on.';
  if (state?.default) {
    const entry = await c.libraryEntry(state.default).catch(() => ({}) as Record<string, string>);
    boots = `${entry['name'] ?? state.default}${entry['version'] ? ` ${entry['version']}` : ''} (library entry ${state.default})`;
  }
  const { config, error } = await savedConfig(c);
  const content = await readText(c, CONTENT_STATE);
  const old = content === null && (await readText(c, CONTENT_STATE_OLD)) !== null;
  return {
    info,
    boots,
    saved: config === undefined ? `Could not read the saved configuration (${error}).` : configText(config),
    content: contentText(content, old),
  };
}

export default function DevicePage() {
  const { data, refetch, isFetching } = useQuery({
    queryKey: ['device'],
    queryFn: loadDevice,
    meta: { action: 'refresh_status' },
  });
  return (
    <section>
      <PageHeader title="Device">What the OP-XY reports right now.</PageHeader>
      {data && (
        <>
          <Card>
            <dl id="device-facts" className="grid grid-cols-[max-content_1fr] gap-x-[22px] gap-y-2">
              <Fact name="XY Forever" fact="version">
                {data.info.version ?? 'unknown'}
              </Fact>
              <Fact name="Image" fact="image">
                {data.info.image ?? 'unknown'}
              </Fact>
              <Fact name="Running">{modeText(data.info.mode)}</Fact>
              <Fact name="Boots next" fact="boots">
                {data.boots}
              </Fact>
              <Fact name="Saved mods">{data.saved}</Fact>
              <Fact name="Content">{data.content}</Fact>
              <Fact name="Protocol">
                <span className="text-muted-foreground">
                  {data.info.proto}
                  {data.info.dev ? ', developer build' : ''}
                </span>
              </Fact>
            </dl>
          </Card>
          <div className="mb-4">
            <Button disabled={isFetching} onClick={() => void refetch()}>
              Refresh
            </Button>
          </div>
          <UpdateCard info={data.info} />
          <UninstallCard info={data.info} />
        </>
      )}
    </section>
  );
}

const Fact = ({ name, fact, children }: { name: string; fact?: string; children: ReactNode }) => (
  <>
    <dt className="text-muted-foreground">{name}</dt>
    <dd data-fact={fact}>{children}</dd>
  </>
);

const CHANNEL = 'xyf.channel',
  PENDING = 'xyf.update';
/** sessionStorage and localStorage may be unavailable (private mode): the choice then lasts this page only. */
function stored(storage: () => Storage, key: string, value?: string | null): string | null {
  try {
    if (value === null) storage().removeItem(key);
    else if (value !== undefined) storage().setItem(key, value);
    return storage().getItem(key);
  } catch {
    return value ?? null;
  }
}

/** An update started in this tab whose restart the page has not confirmed yet (a reload mid-update). */
function pendingUpdate(): ReleasePin | null {
  try {
    return validPin(JSON.parse(stored(() => sessionStorage, PENDING) ?? 'null'));
  } catch {
    return null;
  }
}

function UpdateCard({ info }: { info: DeviceInfo }) {
  const stable = pinned();
  const canary = useCanary();
  const [channel, setChannel] = useState<Channel>(() =>
    stored(() => localStorage, CHANNEL) === 'canary' ? 'canary' : 'stable',
  );
  const [progress, setProgress] = useState(() => {
    // reloaded mid-update: the OP-XY's image now says whether it took the chosen one
    const pending = pendingUpdate();
    if (!pending) return '';
    return info.image === pending.image
      ? `${releaseName(pending)} is running in the menu.`
      : `The update to ${releaseName(pending)} (image ${pending.image}) did not finish: your OP-XY runs image ${info.image ?? 'unknown'}.`;
  });
  const target = channel === 'canary' && canary ? canary : stable;
  const update = useAction('update_xy_forever', async (release: ReleasePin) => {
    stored(() => sessionStorage, PENDING, JSON.stringify(release)); // kept fixed through a reload
    try {
      bugReport.updateStatus(await updateXyf(device(), release, stable, setProgress));
    } catch (error) {
      setProgress('');
      throw error;
    } finally {
      stored(() => sessionStorage, PENDING, null);
    }
    // the restarted OP-XY's own answers: what the card offers next comes from its image, never from this action
    await queryClient.invalidateQueries({ queryKey: ['device'] });
    setProgress(`${releaseName(release)} is running in the menu.`);
  });
  useEffect(() => {
    if (!update.isPending) stored(() => sessionStorage, PENDING, null); // confirmed above
  }, [update.isPending]);
  if (!stable || !target) return null;
  // what to offer: the chosen channel's image against the running one (info, re-read after an update)
  const allowed = updateAvailable(info, target, stable);
  if (!canary && !allowed && !progress) return null;
  const choose = (next: Channel): void => {
    setChannel(next);
    stored(() => localStorage, CHANNEL, next);
    update.reset(); // a new choice: the last update's outcome and progress no longer apply
    setProgress('');
  };
  return (
    <Card>
      <CardTitle>{allowed ? `${releaseName(target)} is available` : 'XY Forever updates'}</CardTitle>
      {canary && (
        <ChannelChoice stable={stable} canary={canary} value={channel} onChange={choose} disabled={update.isPending} />
      )}
      {allowed && <ReleaseIdentity release={target} />}
      {allowed && target.notes && <p>{target.notes}</p>}
      {!allowed && !update.isPending && (
        <p data-testid="update-none" className="text-muted-foreground">
          {info.image === target.image
            ? `Your OP-XY runs ${releaseName(target)}.`
            : `${releaseName(target)} is not an update for your OP-XY.`}
        </p>
      )}
      {allowed && (
        <div>
          <Button variant="default" disabled={update.isPending} onClick={() => update.mutate(target)}>
            Update to {channelOf(target) === 'canary' ? releaseName(target) : target.version}
          </Button>
        </div>
      )}
      <p role="status" data-testid="update-status">
        {progress}
      </p>
    </Card>
  );
}

function UninstallCard({ info }: { info: DeviceInfo }) {
  const [progress, setProgress] = useState('');
  const uninstall = useAction('uninstall_xy_forever', async () => {
    if (
      !(await confirmAction(
        'Uninstall XY Forever?',
        'This restores official OS 1.2.1 and its content, removes your XY Forever library, mods and settings, then restarts your OP-XY.',
        'Uninstall',
        true,
      ))
    )
      return;
    useConnection.setState({ uninstalling: true });
    try {
      await uninstallXyf(device(), setProgress);
    } finally {
      useConnection.setState({ uninstalling: false });
      setProgress('');
    }
    useConnection.setState({ client: null, installed: false, stockRestored: true });
    navigatePath('/install', { replace: true });
  });
  return (
    <Card>
      <CardTitle>Restore stock OS 1.2.1</CardTitle>
      {uninstallAvailable(info) ? (
        <>
          <div>
            <Button variant="danger" disabled={uninstall.isPending} onClick={() => uninstall.mutate()}>
              Uninstall XY Forever
            </Button>
          </div>
          <p role="status">{progress}</p>
        </>
      ) : (
        <p>
          Update XY Forever to use web uninstall. It requires device protocol {UNINSTALL_PROTOCOL} or later and the
          stock restore commands.
        </p>
      )}
    </Card>
  );
}
