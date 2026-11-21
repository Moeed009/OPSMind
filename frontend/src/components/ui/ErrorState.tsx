import { AlertCircle, RefreshCw } from 'lucide-react';
import { Button } from './Button';

export interface ErrorStateProps {
  title?: string;
  message: string;
  onRetry?: () => void;
}

export function ErrorState({
  title = 'Something went wrong',
  message,
  onRetry,
}: ErrorStateProps) {
  return (
    <div
      role="alert"
      className="flex flex-col items-center justify-center rounded-[var(--radius-xl)] border border-[var(--danger)]/30 bg-[var(--danger-soft)] p-8 text-center"
    >
      <div className="flex h-10 w-10 items-center justify-center rounded-[var(--radius-lg)] bg-[var(--surface)] border border-[var(--danger)]/30 text-[var(--danger)]">
        <AlertCircle className="h-5 w-5" />
      </div>
      <h4 className="mt-3 text-sm font-semibold text-[var(--text)]">{title}</h4>
      <p className="mt-1 max-w-md text-xs text-[var(--text-muted)]">{message}</p>
      {onRetry && (
        <Button variant="secondary" size="sm" onClick={onRetry} className="mt-4">
          <RefreshCw className="h-3.5 w-3.5" />
          Try again
        </Button>
      )}
    </div>
  );
}
