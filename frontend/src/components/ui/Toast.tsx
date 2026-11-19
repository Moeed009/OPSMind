import { useCallback, useState, type ReactNode } from 'react';
import { AlertCircle, AlertTriangle, CheckCircle2, Info, X } from 'lucide-react';
import { ToastContext, type ToastInput, type ToastVariant } from './toastContext';

interface ToastItem extends ToastInput {
  id: string;
}

const VARIANT_META: Record<ToastVariant, { icon: typeof Info; cls: string }> = {
  default: {
    icon: Info,
    cls: 'border-[var(--border)] bg-[var(--surface)] text-[var(--text)]',
  },
  success: {
    icon: CheckCircle2,
    cls: 'border-[var(--success)]/35 bg-[var(--surface)] text-[var(--text)]',
  },
  warning: {
    icon: AlertTriangle,
    cls: 'border-[var(--warning)]/35 bg-[var(--surface)] text-[var(--text)]',
  },
  danger: {
    icon: AlertCircle,
    cls: 'border-[var(--danger)]/35 bg-[var(--surface)] text-[var(--text)]',
  },
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<ToastItem[]>([]);

  const dismiss = useCallback((id: string) => {
    setItems((prev) => prev.filter((item) => item.id !== id));
  }, []);

  const toast = useCallback(
    ({ durationMs = 4000, ...rest }: ToastInput) => {
      const id = `${Date.now()}-${Math.random().toString(36).slice(2, 7)}`;
      setItems((prev) => [...prev, { id, ...rest }]);
      if (durationMs > 0) {
        window.setTimeout(() => dismiss(id), durationMs);
      }
    },
    [dismiss],
  );

  return (
    <ToastContext.Provider value={{ toast, dismiss }}>
      {children}
      <div
        aria-live="polite"
        className="fixed bottom-4 right-4 z-50 flex w-full max-w-sm flex-col gap-2 pointer-events-none"
      >
        {items.map((item) => {
          const meta = VARIANT_META[item.variant ?? 'default'];
          const Icon = meta.icon;
          return (
            <div
              key={item.id}
              role="status"
              className={`pointer-events-auto flex items-start gap-3 rounded-[var(--radius-lg)] border p-3.5 shadow-[var(--shadow-md)] ${meta.cls}`}
            >
              <Icon className="h-4 w-4 shrink-0 mt-0.5 text-[var(--primary)]" />
              <div className="flex-1 min-w-0">
                <p className="text-xs font-semibold text-[var(--text)]">{item.title}</p>
                {item.description && (
                  <p className="mt-0.5 text-xs text-[var(--text-muted)]">{item.description}</p>
                )}
              </div>
              <button
                type="button"
                aria-label="Dismiss notification"
                onClick={() => dismiss(item.id)}
                className="rounded p-0.5 text-[var(--text-subtle)] hover:text-[var(--text)] cursor-pointer"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}
