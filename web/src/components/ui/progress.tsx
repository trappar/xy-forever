// shadcn/ui Progress: value null shows work without a known total.
import { Progress as ProgressPrimitive } from 'radix-ui';
import type { ComponentProps } from 'react';
import { cn } from '@/lib/utils.ts';

export function Progress({ className, value, ...props }: ComponentProps<typeof ProgressPrimitive.Root>) {
  return (
    <ProgressPrimitive.Root
      data-slot="progress"
      value={value}
      className={cn('relative h-2 w-full overflow-hidden rounded-full bg-secondary', className)}
      {...props}
    >
      <ProgressPrimitive.Indicator
        className={cn('h-full w-full flex-1 bg-primary transition-all', value === null && 'animate-pulse')}
        style={{ transform: `translateX(-${100 - (value ?? 100)}%)` }}
      />
    </ProgressPrimitive.Root>
  );
}
