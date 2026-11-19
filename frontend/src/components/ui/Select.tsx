import { useId, type ReactNode, type SelectHTMLAttributes } from 'react';

const BASE_SELECT_CLASSES =
  'block w-full rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm text-[var(--text)] shadow-[var(--shadow-xs)] transition-colors duration-150 hover:border-[var(--border-hover)] focus:border-[var(--primary)] focus:ring-2 focus:ring-[var(--primary)]/20 focus:outline-none disabled:bg-[var(--surface-muted)] disabled:text-[var(--text-subtle)] disabled:cursor-not-allowed';

export interface SelectProps extends SelectHTMLAttributes<HTMLSelectElement> {
  label?: string;
  hint?: string;
  error?: string;
  children: ReactNode;
}

export function Select({
  label,
  hint,
  error,
  children,
  className = '',
  id: providedId,
  ...rest
}: SelectProps) {
  const generatedId = useId();
  const id = providedId ?? generatedId;

  return (
    <div className="space-y-1.5">
      {label && (
        <label htmlFor={id} className="block text-xs font-medium text-[var(--text-muted)]">
          {label}
        </label>
      )}
      <select
        id={id}
        aria-invalid={error ? true : undefined}
        aria-describedby={error ? `${id}-error` : hint ? `${id}-hint` : undefined}
        className={`${BASE_SELECT_CLASSES} ${
          error ? 'border-[var(--danger)] focus:border-[var(--danger)] focus:ring-[var(--danger)]/20' : ''
        } ${className}`}
        {...rest}
      >
        {children}
      </select>
      {error ? (
        <p id={`${id}-error`} className="text-xs font-medium text-[var(--danger)]">
          {error}
        </p>
      ) : hint ? (
        <p id={`${id}-hint`} className="text-xs text-[var(--text-subtle)]">
          {hint}
        </p>
      ) : null}
    </div>
  );
}

export interface SelectFieldProps extends SelectProps {
  label: string;
}

export function SelectField(props: SelectFieldProps) {
  return <Select {...props} />;
}
