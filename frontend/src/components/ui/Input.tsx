import { useId, useState, type InputHTMLAttributes } from 'react';
import { Eye, EyeOff } from 'lucide-react';

const BASE_INPUT_CLASSES =
  'block w-full rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface)] px-3 py-2 text-sm text-[var(--text)] placeholder:text-[var(--text-subtle)] shadow-[var(--shadow-xs)] transition-colors duration-150 hover:border-[var(--border-hover)] focus:border-[var(--primary)] focus:ring-2 focus:ring-[var(--primary)]/20 focus:outline-none disabled:bg-[var(--surface-muted)] disabled:text-[var(--text-subtle)] disabled:cursor-not-allowed';

export interface InputProps extends InputHTMLAttributes<HTMLInputElement> {
  label?: string;
  hint?: string;
  error?: string;
  showPasswordToggle?: boolean;
}

export function Input({
  label,
  hint,
  error,
  showPasswordToggle = false,
  type,
  className = '',
  id: providedId,
  ...rest
}: InputProps) {
  const generatedId = useId();
  const id = providedId ?? generatedId;
  const [revealed, setRevealed] = useState(false);

  const isPasswordField = type === 'password' && showPasswordToggle;
  const effectiveType = isPasswordField ? (revealed ? 'text' : 'password') : type;

  return (
    <div className="space-y-1.5">
      {label && (
        <label htmlFor={id} className="block text-xs font-medium text-[var(--text-muted)]">
          {label}
        </label>
      )}
      <div className="relative">
        <input
          id={id}
          type={effectiveType}
          aria-invalid={error ? true : undefined}
          aria-describedby={error ? `${id}-error` : hint ? `${id}-hint` : undefined}
          className={`${BASE_INPUT_CLASSES} ${isPasswordField ? 'pr-9' : ''} ${
            error ? 'border-[var(--danger)] focus:border-[var(--danger)] focus:ring-[var(--danger)]/20' : ''
          } ${className}`}
          {...rest}
        />
        {isPasswordField && (
          <button
            type="button"
            onClick={() => setRevealed((prev) => !prev)}
            aria-label={revealed ? 'Hide password' : 'Show password'}
            className="absolute right-2.5 top-1/2 -translate-y-1/2 rounded p-1 text-[var(--text-subtle)] hover:text-[var(--text)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)] cursor-pointer"
          >
            {revealed ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
          </button>
        )}
      </div>
      {error ? (
        <p id={`${id}-error`} role="alert" className="text-xs font-medium text-[var(--danger)]">
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

export interface FieldProps extends InputProps {
  label: string;
}

export function Field(props: FieldProps) {
  return <Input {...props} />;
}
