// Connect, in Manage without a connection (pages/manage.tsx): Web Serial, or the simulator with ?sim=PORT; a clear
// message in browsers without Web Serial. It looks for a granted port that answers when it shows and when a port
// appears (detect.ts); the chooser opens only from the Connect click.
import { useEffect } from 'react';
import { Link, useSearch } from '@tanstack/react-router';
import { Button } from '@/components/ui/button.tsx';
import { Card, CardTitle, CardDescription } from '@/components/ui/card.tsx';
import { PageHeader } from '@/components/page-header.tsx';
import { watchForDevice } from '../detect.ts';
import { adopt, canConnect, openDevice, useAction } from '../device.ts';

export default function ConnectPage() {
  const { sim } = useSearch({ strict: false });
  const connect = useAction(
    'connect',
    async () => {
      adopt(await openDevice());
    },
    'Looking for the OP-XY...',
  );
  useEffect(() => watchForDevice((navigator as Navigator & { serial?: EventTarget }).serial), []);
  return (
    <section>
      <PageHeader title="Connect your OP-XY">
        Switch the OP-XY on while holding PLAY, then connect it by USB. A computer that connected before finds it by
        itself; otherwise press Connect and choose XY Forever in the list. Not installed yet?{' '}
        <Link to="/install/" className="text-primary underline">
          Install XY Forever
        </Link>
        .
      </PageHeader>
      <Card>
        {canConnect() ? (
          <div className="flex flex-wrap items-center gap-2.5">
            <Button variant="default" disabled={connect.isPending} onClick={() => connect.mutate()}>
              Connect
            </Button>
            <span className="text-muted-foreground">
              {sim ? `Simulator on 127.0.0.1:${sim} (development)` : 'Web Serial over USB'}
            </span>
          </div>
        ) : (
          <>
            <CardTitle>This browser cannot talk to the OP-XY</CardTitle>
            <CardDescription>
              XY Forever needs Web Serial, which only desktop Chrome (and Edge) have. Open this page in Chrome on a
              computer. Safari, Firefox and phones are not supported.
            </CardDescription>
          </>
        )}
      </Card>
    </section>
  );
}
