import type { HTMLAttributes, ReactNode, TableHTMLAttributes, TdHTMLAttributes, ThHTMLAttributes } from 'react';

export function Table({ className = '', children, ...rest }: TableHTMLAttributes<HTMLTableElement>) {
  return (
    <div className="w-full overflow-x-auto rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)]">
      <table {...rest} className={`w-full border-collapse text-left text-sm ${className}`}>
        {children}
      </table>
    </div>
  );
}

export function TableHeader({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <thead className={`border-b border-[var(--border)] bg-[var(--surface-muted)] ${className}`}>
      {children}
    </thead>
  );
}

export function TableBody({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <tbody className={`divide-y divide-[var(--border)] ${className}`}>{children}</tbody>;
}

export function TableRow({
  children,
  className = '',
  ...rest
}: HTMLAttributes<HTMLTableRowElement> & { children: ReactNode }) {
  return (
    <tr
      {...rest}
      className={`transition-colors hover:bg-[var(--surface-muted)]/60 ${className}`}
    >
      {children}
    </tr>
  );
}

export function TableHead({
  children,
  className = '',
  ...rest
}: ThHTMLAttributes<HTMLTableCellElement> & { children?: ReactNode }) {
  return (
    <th
      {...rest}
      className={`px-4 py-3 text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)] ${className}`}
    >
      {children}
    </th>
  );
}

export function TableCell({
  children,
  className = '',
  ...rest
}: TdHTMLAttributes<HTMLTableCellElement> & { children?: ReactNode }) {
  return (
    <td {...rest} className={`px-4 py-3.5 text-sm text-[var(--text)] ${className}`}>
      {children}
    </td>
  );
}
