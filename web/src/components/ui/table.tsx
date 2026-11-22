// shadcn/ui Table.
import type { ComponentProps } from 'react';
import { cn } from '@/lib/utils.ts';

export const Table = ({ className, ...props }: ComponentProps<'table'>) => (
  <div className="relative w-full overflow-x-auto">
    <table data-slot="table" className={cn('w-full caption-bottom border-collapse', className)} {...props} />
  </div>
);
export const TableHeader = (props: ComponentProps<'thead'>) => <thead data-slot="table-header" {...props} />;
export const TableBody = ({ className, ...props }: ComponentProps<'tbody'>) => (
  <tbody data-slot="table-body" className={cn('[&_tr:last-child]:border-0', className)} {...props} />
);
export const TableRow = ({ className, ...props }: ComponentProps<'tr'>) => (
  <tr data-slot="table-row" className={cn('border-b', className)} {...props} />
);
export const TableHead = ({ className, ...props }: ComponentProps<'th'>) => (
  <th
    data-slot="table-head"
    className={cn('px-2 py-1.5 text-left text-[13px] font-medium text-muted-foreground', className)}
    {...props}
  />
);
export const TableCell = ({ className, ...props }: ComponentProps<'td'>) => (
  <td data-slot="table-cell" className={cn('px-2 py-[7px] align-middle', className)} {...props} />
);
