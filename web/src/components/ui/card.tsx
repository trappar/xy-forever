// shadcn/ui Card.
import type { ComponentProps } from 'react';
import { cn } from '@/lib/utils.ts';

export const Card = ({ className, ...props }: ComponentProps<'div'>) =>
  <div data-slot="card" className={cn('mb-4 flex flex-col gap-3 rounded-lg border bg-card p-[18px] text-card-foreground', className)} {...props} />;
export const CardHeader = ({ className, ...props }: ComponentProps<'div'>) =>
  <div data-slot="card-header" className={cn('flex flex-wrap items-center gap-2.5', className)} {...props} />;
export const CardTitle = ({ className, ...props }: ComponentProps<'h2'>) =>
  <h2 data-slot="card-title" className={cn('text-[15px] font-semibold', className)} {...props} />;
export const CardDescription = ({ className, ...props }: ComponentProps<'p'>) =>
  <p data-slot="card-description" className={cn('text-muted-foreground', className)} {...props} />;
