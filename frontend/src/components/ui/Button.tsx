import type { ButtonHTMLAttributes } from 'react';
import { Loader2 } from 'lucide-react';

export type ButtonVariant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'outline';
export type ButtonSize = 'sm' | 'md' | 'lg';

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  loading?: boolean;
  variant?: ButtonVariant;
  size?: ButtonSize;
}

const VARIANT_STYLES: Record<ButtonVariant, string> = {
  primary:
    'bg-gradient-to-b from-[var(--primary)] to-[var(--primary-hover)] hover:opacity-90 text-[var(--primary-foreground)] border border-transparent shadow-[0_1px_2px_rgba(0,0,0,0.12)] active:scale-[0.98] active:shadow-none',
  secondary:
    'bg-[var(--surface)] border-[var(--border)] hover:border-[var(--border-hover)] hover:bg-[var(--surface-muted)] text-[var(--text)] border shadow-[var(--shadow-xs)]',
  ghost:
    'bg-transparent hover:bg-[var(--surface-muted)] text-[var(--text-subtle)] hover:text-[var(--text)] border border-transparent',
  danger:
    'bg-[var(--danger)] hover:opacity-90 text-white border border-transparent shadow-[var(--shadow-xs)] active:scale-[0.98]',
  outline:
    'bg-transparent hover:bg-[var(--surface-muted)] text-[var(--text)] border border-[var(--border)] hover:border-[var(--border-hover)]',
};

const SIZE_STYLES: Record<ButtonSize, string> = {
  sm: 'h-8 px-3 text-xs rounded-[var(--radius-md)] gap-1.5 font-medium',
  md: 'h-9 px-4 text-sm rounded-[var(--radius-md)] gap-2 font-medium',
  lg: 'h-10 px-5 text-sm rounded-[var(--radius-lg)] gap-2 font-semibold',
};

export function Button({
  loading = false,
  variant = 'primary',
  size = 'md',
  className = '',
  disabled,
  children,
  type = 'button',
  ...rest
}: ButtonProps) {
  return (
    <button
      type={type}
      {...rest}
      disabled={disabled || loading}
      className={`inline-flex items-center justify-center transition-all duration-150 select-none cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--primary)] focus-visible:ring-offset-1 focus-visible:ring-offset-[var(--bg)] disabled:cursor-not-allowed disabled:opacity-45 ${VARIANT_STYLES[variant]} ${SIZE_STYLES[size]} ${className}`}
    >
      {loading && <Loader2 className={`animate-spin ${size === 'sm' ? 'h-3.5 w-3.5' : 'h-4 w-4'}`} />}
      {children}
    </button>
  );
}
