// shadcn/ui Button.
import { Slot } from 'radix-ui';
import { cva } from 'class-variance-authority';
import type { VariantProps } from 'class-variance-authority';
import type { ComponentProps } from 'react';
import { cn } from '@/lib/utils.ts';

export const buttonVariants = cva(
  'inline-flex shrink-0 cursor-pointer items-center justify-center gap-2 whitespace-nowrap rounded-md border text-sm font-medium transition-colors outline-none focus-visible:ring-2 focus-visible:ring-ring/50 disabled:pointer-events-none disabled:opacity-45',
  {
    variants: {
      variant: {
        default: 'border-primary bg-primary font-semibold text-primary-foreground hover:bg-primary/90',
        destructive: 'border-destructive bg-destructive font-semibold text-white hover:bg-destructive/90',
        outline: 'border-border bg-secondary text-foreground hover:border-[#3c4450]',
        danger: 'border-border bg-secondary text-destructive hover:border-destructive',
        ghost: 'border-transparent bg-transparent hover:bg-accent',
        link: 'h-auto border-transparent bg-transparent p-0 text-foreground underline-offset-4 hover:underline',
      },
      size: { default: 'h-9 px-3', sm: 'h-7 px-2 text-[13px]' },
    },
    defaultVariants: { variant: 'outline', size: 'default' },
  },
);

export function Button({ className, variant, size, asChild = false, ...props }: ComponentProps<'button'> & VariantProps<typeof buttonVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot.Root : 'button';
  return <Comp data-slot="button" type="button" className={cn(buttonVariants({ variant, size, className }))} {...props} />;
}
