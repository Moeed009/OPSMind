import type { HTMLAttributes, ReactNode } from 'react';

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  children: ReactNode;
  hoverable?: boolean;
}

export function Card({ children, className = '', hoverable = false, ...rest }: CardProps) {
  return (
    <div
      {...rest}
      className={`rounded-[var(--radius-xl)] border border-[var(--border)] bg-[var(--surface)] shadow-[var(--shadow-sm)] ${
        hoverable
          ? 'hover:shadow-[var(--shadow-md)] hover:border-[var(--border-hover)] transition-all duration-200'
          : ''
      } ${className}`}
    >
      {children}
    </div>
  );
}

export function CardHeader({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`px-5 pt-5 pb-4 flex flex-col gap-1 ${className}`}>{children}</div>;
}

export function CardTitle({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <h3 className={`text-sm font-semibold text-[var(--text)] ${className}`}>{children}</h3>;
}

export function CardDescription({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <p className={`text-xs text-[var(--text-muted)] ${className}`}>{children}</p>;
}

export function CardContent({ children, className = '' }: { children: ReactNode; className?: string }) {
  return <div className={`px-5 pb-5 ${className}`}>{children}</div>;
}

export function CardFooter({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <div className={`px-5 pb-5 pt-4 border-t border-[var(--border)] flex items-center justify-end gap-2 ${className}`}>
      {children}
    </div>
  );
}
