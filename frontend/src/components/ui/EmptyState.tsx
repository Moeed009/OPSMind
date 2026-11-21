import type { ComponentType, ReactNode } from 'react';
import { Inbox } from 'lucide-react';

export interface EmptyStateProps {
  icon?: ComponentType<{ className?: string }>;
  title: string;
  description: string;
  action?: ReactNode;
}

export function EmptyState({ icon: Icon = Inbox, title, description, action }: EmptyStateProps) {
  return (
    <div className="flex flex-col items-center justify-center rounded-[var(--radius-xl)] border border-dashed border-[var(--border)] bg-[var(--surface)]/50 p-10 text-center">
      <div className="flex h-11 w-11 items-center justify-center rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface-muted)] text-[var(--text-muted)]">
        <Icon className="h-5 w-5" />
      </div>
      <h4 className="mt-4 text-sm font-semibold text-[var(--text)]">{title}</h4>
      <p className="mt-1 max-w-sm text-xs leading-relaxed text-[var(--text-muted)]">{description}</p>
      {action && <div className="mt-5">{action}</div>}
    </div>
  );
}
