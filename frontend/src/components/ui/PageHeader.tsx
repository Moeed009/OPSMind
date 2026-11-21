import type { ReactNode } from 'react';

export interface PageHeaderProps {
  title: string;
  description?: string;
  badge?: ReactNode;
  actions?: ReactNode;
}

export function PageHeader({ title, description, badge, actions }: PageHeaderProps) {
  return (
    <div className="flex flex-col gap-4 pb-6 sm:flex-row sm:items-start sm:justify-between">
      <div className="min-w-0">
        <div className="flex items-center gap-2.5 flex-wrap">
          <h1 className="text-[1.375rem] font-bold tracking-tight text-[var(--text)] sm:text-2xl">
            {title}
          </h1>
          {badge}
        </div>
        {description && (
          <p className="mt-1.5 text-sm text-[var(--text-muted)] leading-relaxed">{description}</p>
        )}
      </div>
      {actions && <div className="flex items-center gap-2 shrink-0 flex-wrap">{actions}</div>}
    </div>
  );
}
