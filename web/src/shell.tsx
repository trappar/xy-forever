// The app's frame: navigation, the link status, the bug report, the installed notice, toasts and questions.
import { useState } from 'react';
import type { ReactNode } from 'react';
import { Link, Outlet, useSearch } from '@tanstack/react-router';
import { Toaster } from '@/components/ui/sonner.tsx';
import { Button } from '@/components/ui/button.tsx';
import {
  Dialog,
  DialogClose,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog.tsx';
import { downloadBlob } from '@/lib/download.ts';
import { AskDialog } from './ask.tsx';
import { bugReport } from './bug-report.ts';
import { useConnection } from './connection.ts';

const NAV = [
  ['/connect/', 'Connect'],
  ['/install/', 'Install'],
  ['/device/', 'Device'],
  ['/mods/', 'Mods'],
  ['/files/', 'Files'],
] as const;

export const AppShell = () => (
  <Shell>
    <Outlet />
  </Shell>
);

export function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="grid min-h-screen md:grid-cols-[200px_1fr]">
      <aside className="flex flex-col gap-6 border-b bg-card px-3.5 py-5 md:sticky md:top-0 md:h-screen md:border-r md:border-b-0">
        <div className="flex items-center gap-2 text-[17px] font-semibold tracking-wide">
          <span className="rounded-sm bg-primary px-1.5 py-0.5 text-[13px] font-extrabold text-primary-foreground">
            XY
          </span>
          Forever
        </div>
        <nav aria-label="Pages" className="flex flex-row flex-wrap gap-0.5 md:flex-col">
          {NAV.map(([to, label]) => (
            <Link
              key={to}
              to={to}
              data-page={to.slice(1, -1)}
              className="rounded-md px-2.5 py-1.5 text-muted-foreground no-underline hover:bg-secondary hover:text-foreground"
              activeProps={{ className: 'bg-secondary text-foreground shadow-[inset_2px_0_0_var(--color-primary)]' }}
            >
              {label}
            </Link>
          ))}
        </nav>
        <BugReportButton />
        <LinkStatus />
      </aside>
      <main className="w-full max-w-[1100px] px-[clamp(16px,4vw,44px)] py-7">
        <InstalledNotice />
        {children}
      </main>
      <Toaster />
      <AskDialog />
    </div>
  );
}

function LinkStatus() {
  const connected = useConnection((s) => s.client !== null);
  const { sim } = useSearch({ strict: false });
  return (
    <div className="flex items-center gap-2 text-[13px] text-muted-foreground md:mt-auto">
      <span
        className={
          connected ? 'size-2 rounded-full bg-ok shadow-[0_0_8px_var(--color-ok)]' : 'size-2 rounded-full bg-border'
        }
      />
      <span data-testid="link">{connected ? `Connected${sim ? ' (simulator)' : ''}` : 'Not connected'}</span>
    </div>
  );
}

function InstalledNotice() {
  const installed = useConnection((s) => s.installed);
  if (!installed) return null;
  return (
    <div
      data-testid="installed"
      role="status"
      className="mb-4 flex items-center gap-3 rounded-lg border border-ok/40 bg-ok/10 px-4 py-3"
    >
      <span className="flex-1">
        XY Forever is installed, with your OP-XY's OS 1.2.1 as the firmware it starts. Add mods here.
      </span>
      <Button size="sm" onClick={() => useConnection.setState({ installed: false })}>
        Dismiss
      </Button>
    </div>
  );
}

/** Bug report: a preview of the JSON bundle, and a download of that same snapshot. */
function BugReportButton() {
  const [json, setJson] = useState<string | null>(null);
  const open = (): void => setJson(bugReport.snapshot(useConnection.getState().client?.connected === true));
  return (
    <>
      <Button className="justify-start" onClick={open}>
        Bug report
      </Button>
      <Dialog
        open={json !== null}
        onOpenChange={(o) => {
          if (!o) setJson(null);
        }}
      >
        <DialogContent className="sm:max-w-2xl">
          <DialogHeader>
            <DialogTitle>Bug report preview</DialogTitle>
            <DialogDescription>
              Review this JSON before sharing. Device details are from the last successful connection or refresh in this
              tab.
            </DialogDescription>
          </DialogHeader>
          <pre
            className="max-h-[50vh] overflow-auto rounded-md border bg-background p-3 font-mono text-xs"
            tabIndex={0}
            aria-label="Bug report JSON"
          >
            {json}
          </pre>
          <DialogFooter>
            <DialogClose asChild>
              <Button>Close</Button>
            </DialogClose>
            <Button
              variant="default"
              onClick={() =>
                downloadBlob(new Blob([json ?? ''], { type: 'application/json' }), 'xy-forever-bug-report.json')
              }
            >
              Download JSON
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
