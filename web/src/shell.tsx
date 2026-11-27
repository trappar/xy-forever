// The app's frame: navigation (two sections, Install and Manage, with Manage's pages under it: #316), the link status
// and Disconnect, the bug report, the installed and stock-restored notices, toasts and questions.
import { useState } from 'react';
import type { ReactNode } from 'react';
import { Link, Outlet, useLocation, useSearch } from '@tanstack/react-router';
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
import { disconnect, useAction } from './device.ts';

const SECTIONS = [
  ['/install/', 'Install'],
  ['/manage/', 'Manage'],
] as const;
const MANAGE = [
  ['/manage/', 'Device'],
  ['/manage/mods/', 'Mods'],
  ['/manage/files/', 'Files'],
] as const;
const LINK = 'rounded-md px-2.5 py-1.5 text-muted-foreground no-underline hover:bg-secondary hover:text-foreground';
const ACTIVE = 'bg-secondary text-foreground shadow-[inset_2px_0_0_var(--color-primary)]';
/** The page's name for tests and the smoke run: install, manage, manage/mods, manage/files. */
const pageName = (to: string): string => to.slice(1, -1);

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
        <Nav />
        <BugReportButton />
        <LinkStatus />
      </aside>
      <main className="w-full max-w-[1100px] px-[clamp(16px,4vw,44px)] py-7">
        <InstalledNotice />
        <StockRestoredNotice />
        {children}
      </main>
      <Toaster />
      <AskDialog />
    </div>
  );
}

/** Install and Manage; Manage's pages show under it while it is open. */
function Nav() {
  const managing = useLocation({ select: (l) => l.pathname.includes('/manage/') });
  return (
    <nav aria-label="Pages" className="flex flex-row flex-wrap gap-0.5 md:flex-col">
      {SECTIONS.map(([to, label]) => (
        <div key={to} className="contents">
          <Link to={to} data-page={pageName(to)} className={`${LINK} font-medium`} activeProps={{ className: ACTIVE }}>
            {label}
          </Link>
          {to === '/manage/' &&
            managing &&
            MANAGE.map(([page, name]) => (
              <Link
                key={page}
                to={page}
                data-page={page === '/manage/' ? 'manage/device' : pageName(page)}
                className={`${LINK} text-sm md:ml-3 data-[status=active]:font-medium data-[status=active]:text-foreground data-[status=active]:shadow-[inset_2px_0_0_var(--color-primary)]`}
                activeOptions={{ exact: true }}
              >
                {name}
              </Link>
            ))}
        </div>
      ))}
    </nav>
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
      {connected && <DisconnectButton />}
    </div>
  );
}

function DisconnectButton() {
  const close = useAction('disconnect', disconnect);
  return (
    <Button size="sm" className="ml-auto" disabled={close.isPending} onClick={() => close.mutate()}>
      Disconnect
    </Button>
  );
}

/** After an uninstall, until the next connection. */
function StockRestoredNotice() {
  const restored = useConnection((s) => s.stockRestored);
  if (!restored) return null;
  return (
    <p data-testid="stock-restored" role="status" className="mb-4 rounded-lg border bg-card px-3.5 py-2.5">
      Your OP-XY is running stock OS 1.2.1.
    </p>
  );
}

function InstalledNotice() {
  const installed = useConnection((s) => s.installed);
  const cleaned = useConnection((s) => s.cleaned);
  if (!installed) return null;
  return (
    <div
      data-testid="installed"
      role="status"
      className="mb-4 flex items-center gap-3 rounded-lg border border-ok/40 bg-ok/10 px-4 py-3"
    >
      <span className="flex-1">
        XY Forever is installed, with your OP-XY's OS 1.2.1 as the firmware it starts. Add mods here.
        {cleaned && <span data-testid="cleaned"> {cleaned}</span>}
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
