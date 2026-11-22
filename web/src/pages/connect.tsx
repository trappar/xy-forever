// Connect: Web Serial, or the simulator with ?sim=PORT; a clear message in browsers without Web Serial. The port opens
// only from the Connect click (the chooser when no granted port answers).
import { Link, useNavigate, useSearch } from '@tanstack/react-router';
import { Button } from '@/components/ui/button.tsx';
import { Card, CardTitle, CardDescription } from '@/components/ui/card.tsx';
import { PageHeader } from '@/components/page-header.tsx';
import { useConnection } from '../connection.ts';
import { adopt, canConnect, disconnect, openDevice, useAction } from '../device.ts';

export default function ConnectPage() {
  const client = useConnection((s) => s.client);
  const stockRestored = useConnection((s) => s.stockRestored);
  const { sim } = useSearch({ strict: false });
  const navigate = useNavigate();
  const connect = useAction(
    'connect',
    async () => {
      adopt(await openDevice());
    },
    'Looking for the OP-XY...',
  );
  const close = useAction('disconnect', disconnect);
  return (
    <section>
      {stockRestored && (
        <p role="status" className="mb-4 rounded-lg border bg-card px-3.5 py-2.5">
          Stock OS 1.2.1 was restored and your OP-XY was asked to restart.
        </p>
      )}
      <PageHeader title="Connect your OP-XY">
        Start the OP-XY into the XY Forever menu, connect it by USB, then press Connect and choose XY Forever in the
        list. Not installed yet?{' '}
        <Link to="/install/" className="text-primary underline">
          Install XY Forever
        </Link>
        .
      </PageHeader>
      <Card>
        {canConnect() ? (
          <div className="flex flex-wrap items-center gap-2.5">
            {client ? (
              <>
                <span>Connected to XY Forever {client.info.version ?? '?'}.</span>
                <Button onClick={() => close.mutate()}>Disconnect</Button>
              </>
            ) : (
              <Button
                variant="default"
                disabled={connect.isPending}
                onClick={() => connect.mutate(undefined, { onSuccess: () => void navigate({ to: '/device/' }) })}
              >
                Connect
              </Button>
            )}
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
