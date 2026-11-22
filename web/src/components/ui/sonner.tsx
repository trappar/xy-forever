// shadcn/ui Toaster (sonner), dark.
import { Toaster as Sonner } from 'sonner';
import type { ToasterProps } from 'sonner';

export function Toaster(props: ToasterProps) {
  return (
    <Sonner
      theme="dark"
      position="bottom-right"
      closeButton
      style={
        {
          '--normal-bg': 'var(--color-popover)',
          '--normal-text': 'var(--color-popover-foreground)',
          '--normal-border': 'var(--color-border)',
        } as React.CSSProperties
      }
      {...props}
    />
  );
}
