// shadcn/ui Badge.
import { cva } from 'class-variance-authority';
import type { VariantProps } from 'class-variance-authority';
import type { ComponentProps } from 'react';
import { cn } from '@/lib/utils.ts';

const badgeVariants = cva('inline-flex items-center rounded-full border px-1.5 py-px text-xs', {
  variants: {
    variant: {
      default: 'border-border text-muted-foreground',
      ok: 'border-ok/50 text-ok',
      warn: 'border-warn/50 text-warn',
    },
  },
  defaultVariants: { variant: 'default' },
});

export const Badge = ({
  className,
  variant,
  ...props
}: ComponentProps<'span'> & VariantProps<typeof badgeVariants>) => (
  <span data-slot="badge" className={cn(badgeVariants({ variant }), className)} {...props} />
);
