// shadcn/ui Checkbox.
import { Checkbox as CheckboxPrimitive } from 'radix-ui';
import type { ComponentProps } from 'react';
import { cn } from '@/lib/utils.ts';

export function Checkbox({ className, ...props }: ComponentProps<typeof CheckboxPrimitive.Root>) {
  return (
    <CheckboxPrimitive.Root
      data-slot="checkbox"
      className={cn(
        'peer grid size-4 shrink-0 cursor-pointer place-content-center rounded-sm border border-input bg-background outline-none focus-visible:ring-2 focus-visible:ring-ring/50 data-[state=checked]:border-primary data-[state=checked]:bg-primary data-[state=checked]:text-primary-foreground',
        className,
      )}
      {...props}
    >
      <CheckboxPrimitive.Indicator className="text-[11px] leading-none font-bold">✓</CheckboxPrimitive.Indicator>
    </CheckboxPrimitive.Root>
  );
}
