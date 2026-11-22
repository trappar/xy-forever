// A page's heading and the line under it.
import type { ReactNode } from 'react';

export function PageHeader({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <header className="mb-[22px]">
      <h1 className="mb-1 text-[22px] font-semibold">{title}</h1>
      {children && <p className="max-w-[62ch] text-muted-foreground">{children}</p>}
    </header>
  );
}
