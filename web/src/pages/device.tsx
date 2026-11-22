// Device: build, what is running, what boots next, the saved mods, the content state; Update when a newer pinned
// release exists, and Uninstall (restores stock OS 1.2.1, then the app goes back to Connect).
import { useState } from 'react';
import type { ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { DeviceInfo } from 'xy-forever-client';
import { Button } from '@/components/ui/button.tsx';
import { Card, CardTitle } from '@/components/ui/card.tsx';
import { PageHeader } from '@/components/page-header.tsx';
import { confirmAction } from '../ask.tsx';
import { bugReport } from '../bug-report.ts';
import { useConnection } from '../connection.ts';
import { device, queryClient, readText, savedConfig, useAction } from '../device.ts';
import { pinned } from '../install/release.ts';
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
    const entry = await c.libraryEntry(state.default).catch(() => ({} as Record<string, string>));
    boots = `${entry['name'] ?? state.default}${entry['version'] ? ` ${entry['version']}` : ''} (library entry ${state.default})`;
  }
  const { config, error } = await savedConfig(c);
  const content = await readText(c, CONTENT_STATE);
  const old = content === null && await readText(c, CONTENT_STATE_OLD) !== null;
  return { info, boots, saved: config === undefined ? `Could not read the saved configuration (${error}).` : configText(config), content: contentText(content, old) };
}
type DeviceData = Awaited<ReturnType<typeof loadDevice>>;

export default function DevicePage() {
  const { data, refetch, isFetching } = useQuery({ queryKey: ['device'], queryFn: loadDevice, meta: { action: 'refresh_status' } });
  return (
    <section>
      <PageHeader title="Device">What the OP-XY reports right now.</PageHeader>
      {data && <>
        <Card>
          <dl id="device-facts" className="grid grid-cols-[max-content_1fr] gap-x-[22px] gap-y-2">
            <Fact name="XY Forever" fact="version">{data.info.version ?? 'unknown'}</Fact>
            <Fact name="Image" fact="image">{data.info.image ?? 'unknown'}</Fact>
            <Fact name="Running">{modeText(data.info.mode)}</Fact>
            <Fact name="Boots next" fact="boots">{data.boots}</Fact>
            <Fact name="Saved mods">{data.saved}</Fact>
            <Fact name="Content">{data.content}</Fact>
            <Fact name="Protocol"><span className="text-muted-foreground">{data.info.proto}{data.info.dev ? ', developer build' : ''}</span></Fact>
          </dl>
        </Card>
        <div className="mb-4"><Button disabled={isFetching} onClick={() => void refetch()}>Refresh</Button></div>
        <UpdateCard info={data.info} />
        <UninstallCard info={data.info} />
      </>}
    </section>
  );
}

const Fact = ({ name, fact, children }: { name: string; fact?: string; children: ReactNode }) =>
  <><dt className="text-muted-foreground">{name}</dt><dd data-fact={fact}>{children}</dd></>;

function UpdateCard({ info }: { info: DeviceInfo }) {
  const release = pinned();
  const [progress, setProgress] = useState('');
  const update = useAction('update_xy_forever', async () => {
    try {
      bugReport.updateStatus(await updateXyf(device(), release!, setProgress));
    } catch (error) { setProgress(''); throw error; }
    queryClient.setQueryData<DeviceData>(['device'], (d) => d && { ...d, info: { ...d.info, version: release!.version, image: release!.image } });
    setProgress(`XY Forever ${release!.version} is running in the menu.`);
  });
  if (!release || (!updateAvailable(info, release) && !update.isSuccess)) return null;
  return (
    <Card>
      <CardTitle>XY Forever {release.version} is available</CardTitle>
      {release.notes && <p>{release.notes}</p>}
      {!update.isSuccess && <div><Button variant="default" disabled={update.isPending} onClick={() => update.mutate()}>Update to {release.version}</Button></div>}
      <p role="status" data-testid="update-status">{progress}</p>
    </Card>
  );
}

function UninstallCard({ info }: { info: DeviceInfo }) {
  const [progress, setProgress] = useState('');
  const uninstall = useAction('uninstall_xy_forever', async () => {
    if (!await confirmAction('Uninstall XY Forever?',
      'This restores official OS 1.2.1 and its content, removes your XY Forever library, mods and settings, then restarts your OP-XY.',
      'Uninstall', true)) return;
    useConnection.setState({ uninstalling: true });
    try { await uninstallXyf(device(), setProgress); } finally { useConnection.setState({ uninstalling: false }); setProgress(''); }
    useConnection.setState({ client: null, installed: false, stockRestored: true });
    navigatePath('/connect', { replace: true });
  });
  return (
    <Card>
      <CardTitle>Restore stock OS 1.2.1</CardTitle>
      {uninstallAvailable(info) ? <>
        <div><Button variant="danger" disabled={uninstall.isPending} onClick={() => uninstall.mutate()}>Uninstall XY Forever</Button></div>
        <p role="status">{progress}</p>
      </> : <p>Update XY Forever to use web uninstall. It requires device protocol {UNINSTALL_PROTOCOL} or later and the stock restore commands.</p>}
    </Card>
  );
}
