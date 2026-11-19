import type { ReactNode } from 'react';
import type { Role } from '../../lib/types';

export type BadgeVariant =
  | 'neutral'
  | 'primary'
  | 'success'
  | 'warning'
  | 'danger'
  | 'indigo'
  | 'emerald'
  | 'amber'
  | 'rose'
  | 'sky'
  | 'purple'
  | 'slate';

export type BadgeSize = 'xs' | 'sm' | 'md';

const VARIANT_STYLES: Record<BadgeVariant, string> = {
  neutral: 'bg-[var(--surface-muted)] text-[var(--text-muted)] border-[var(--border)]',
  slate: 'bg-[var(--surface-muted)] text-[var(--text-muted)] border-[var(--border)]',
  primary: 'bg-[var(--primary-soft)] text-[var(--primary)] border-[var(--primary)]/25',
  indigo: 'bg-[var(--primary-soft)] text-[var(--primary)] border-[var(--primary)]/25',
  success: 'bg-[var(--success-soft)] text-[var(--success)] border-[var(--success)]/25',
  emerald: 'bg-[var(--success-soft)] text-[var(--success)] border-[var(--success)]/25',
  warning: 'bg-[var(--warning-soft)] text-[var(--warning)] border-[var(--warning)]/25',
  amber: 'bg-[var(--warning-soft)] text-[var(--warning)] border-[var(--warning)]/25',
  danger: 'bg-[var(--danger-soft)] text-[var(--danger)] border-[var(--danger)]/25',
  rose: 'bg-[var(--danger-soft)] text-[var(--danger)] border-[var(--danger)]/25',
  sky: 'bg-sky-500/10 text-sky-400 border-sky-500/25',
  purple: 'bg-purple-500/10 text-purple-400 border-purple-500/25',
};

const SIZE_STYLES: Record<BadgeSize, string> = {
  xs: 'px-1.5 py-0.5 text-[10px]',
  sm: 'px-2.5 py-0.5 text-xs',
  md: 'px-3 py-1 text-xs',
};

export function Badge({
  children,
  variant = 'neutral',
  size = 'sm',
  className = '',
}: {
  children: ReactNode;
  variant?: BadgeVariant;
  size?: BadgeSize;
  className?: string;
}) {
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border font-medium tracking-wide ${VARIANT_STYLES[variant]} ${SIZE_STYLES[size]} ${className}`}
    >
      {children}
    </span>
  );
}

export function RoleBadge({ role }: { role: Role }) {
  const map: Record<Role, BadgeVariant> = {
    owner: 'primary',
    admin: 'sky',
    builder: 'warning',
    reviewer: 'success',
    viewer: 'neutral',
  };

  return (
    <Badge variant={map[role] ?? 'neutral'} size="xs" className="capitalize">
      {role}
    </Badge>
  );
}
