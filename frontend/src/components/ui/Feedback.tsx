import type { ComponentType, ReactNode } from 'react';
import {
  AlertCircle,
  AlertTriangle,
  CheckCircle2,
  Info,
  Moon,
  ShieldCheck,
  Sparkles,
  Sun,
  X,
} from 'lucide-react';
import { useTheme } from '../../lib/themeContext';

export function Spinner({ className = 'h-5 w-5' }: { className?: string }) {
  return (
    <svg
      className={`animate-spin ${className}`}
      viewBox="0 0 24 24"
      fill="none"
      role="status"
      aria-label="Loading"
    >
      <circle className="opacity-20" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="3" />
      <path className="opacity-90" fill="currentColor" d="M4 12a8 8 0 018-8v4a4 4 0 00-4 4H4z" />
    </svg>
  );
}

export function FullPageSpinner() {
  return (
    <div className="flex min-h-screen items-center justify-center bg-[var(--bg)]">
      <div className="flex flex-col items-center gap-3">
        <div className="flex h-12 w-12 items-center justify-center rounded-[var(--radius-xl)] bg-gradient-to-br from-[var(--primary)] to-[#a78bfa] text-white shadow-[var(--shadow-sm)]">
          <Spinner className="h-6 w-6 text-white" />
        </div>
        <div className="text-center">
          <p className="text-base font-semibold text-[var(--text)]">Loading OpsMind</p>
          <p className="text-xs text-[var(--text-subtle)]">Connecting to workspace...</p>
        </div>
      </div>
    </div>
  );
}

export function Alert({
  kind = 'error',
  onClose,
  children,
}: {
  kind?: 'error' | 'success' | 'info' | 'warning';
  onClose?: () => void;
  children: ReactNode;
}) {
  const config = {
    error: {
      container: 'bg-[var(--danger-soft)] border-[var(--danger)]/30 text-[var(--danger)] border-l-[var(--danger)]',
      icon: AlertCircle,
    },
    success: {
      container: 'bg-[var(--success-soft)] border-[var(--success)]/30 text-[var(--success)] border-l-[var(--success)]',
      icon: CheckCircle2,
    },
    info: {
      container: 'bg-[var(--primary-soft)] border-[var(--primary)]/30 text-[var(--primary)] border-l-[var(--primary)]',
      icon: Info,
    },
    warning: {
      container: 'bg-[var(--warning-soft)] border-[var(--warning)]/30 text-[var(--warning)] border-l-[var(--warning)]',
      icon: AlertTriangle,
    },
  }[kind];

  const IconComponent = config.icon;

  return (
    <div
      role={kind === 'error' ? 'alert' : 'status'}
      className={`flex items-start gap-3 rounded-[var(--radius-lg)] border border-l-[3px] p-3.5 text-sm ${config.container}`}
    >
      <IconComponent className="h-4 w-4 shrink-0 mt-0.5" />
      <div className="flex-1 text-sm leading-relaxed text-[var(--text)]">{children}</div>
      {onClose && (
        <button
          type="button"
          onClick={onClose}
          className="rounded p-0.5 text-[var(--text-subtle)] hover:text-[var(--text)] transition-colors cursor-pointer"
          aria-label="Dismiss alert"
        >
          <X className="h-3.5 w-3.5" />
        </button>
      )}
    </div>
  );
}

export function StatCard({
  title,
  value,
  subtitle,
  icon: Icon,
  trend,
  colorScheme = 'indigo',
}: {
  title: string;
  value: string | number;
  subtitle?: string;
  icon: ComponentType<{ className?: string }>;
  trend?: string;
  colorScheme?: 'indigo' | 'emerald' | 'amber' | 'sky' | 'purple';
}) {
  const iconCls: Record<string, string> = {
    indigo: 'bg-[var(--primary-soft)] text-[var(--primary)] border-[var(--primary)]/25',
    emerald: 'bg-[var(--success-soft)] text-[var(--success)] border-[var(--success)]/25',
    amber: 'bg-[var(--warning-soft)] text-[var(--warning)] border-[var(--warning)]/25',
    sky: 'bg-sky-500/10 text-sky-400 border-sky-500/25',
    purple: 'bg-purple-500/10 text-purple-400 border-purple-500/25',
  };

  return (
    <div className="group rounded-[var(--radius-xl)] border border-[var(--border)] bg-[var(--surface)] p-5 shadow-[var(--shadow-xs)] transition-all duration-200 hover:shadow-[var(--shadow-md)] hover:border-[var(--border-hover)] hover:-translate-y-0.5">
      <div className="flex items-center justify-between gap-2">
        <span className="text-[11px] font-semibold uppercase tracking-wider text-[var(--text-muted)]">
          {title}
        </span>
        <div
          className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-[var(--radius-lg)] border transition-transform duration-200 group-hover:scale-105 ${
            iconCls[colorScheme] ?? iconCls.indigo
          }`}
        >
          <Icon className="h-4 w-4" />
        </div>
      </div>
      <div className="mt-3 flex items-baseline gap-2.5">
        <span className="font-display text-2xl sm:text-3xl font-bold tracking-tight text-[var(--text)]">
          {value}
        </span>
        {trend && (
          <span className="inline-flex items-center rounded-full bg-[var(--success-soft)] px-2 py-0.5 text-[11px] font-semibold text-[var(--success)]">
            {trend}
          </span>
        )}
      </div>
      {subtitle && <p className="mt-1.5 text-xs text-[var(--text-muted)] truncate">{subtitle}</p>}
    </div>
  );
}

export function AuthLayout({
  title,
  subtitle,
  children,
  footer,
}: {
  title: string;
  subtitle?: string;
  children: ReactNode;
  footer?: ReactNode;
}) {
  const { theme, toggleTheme } = useTheme();

  return (
    <div className="relative flex min-h-screen bg-[var(--bg)] text-[var(--text)]">
      <div className="absolute right-4 top-4 z-20">
        <button
          type="button"
          onClick={toggleTheme}
          aria-label="Toggle theme"
          className="inline-flex h-8 w-8 items-center justify-center rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface)] text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)] transition-colors cursor-pointer"
        >
          {theme === 'dark' ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
        </button>
      </div>

      <aside className="relative hidden lg:flex lg:w-[46%] flex-col justify-between overflow-hidden p-10 bg-[#0b0d17]">
        {/* Ambient gradient orbs & subtle grid */}
        <div
          className="pointer-events-none absolute inset-0 opacity-25"
          style={{
            backgroundImage: 'radial-gradient(circle at 1px 1px, rgba(148,163,184,0.3) 1px, transparent 0)',
            backgroundSize: '26px 26px',
          }}
        />
        <div className="pointer-events-none absolute -left-24 -top-24 h-80 w-80 rounded-full bg-indigo-600/25 blur-3xl" />
        <div className="pointer-events-none absolute -bottom-24 right-0 h-80 w-80 rounded-full bg-violet-600/20 blur-3xl" />

        <div className="relative flex items-center gap-3">
          <div className="flex h-9 w-9 items-center justify-center rounded-[var(--radius-lg)] bg-gradient-to-br from-indigo-500 to-violet-600 shadow-lg shadow-indigo-500/25">
            <Sparkles className="h-4 w-4 text-white" />
          </div>
          <span className="font-display text-lg font-bold tracking-tight text-white">OpsMind</span>
        </div>

        <div className="relative max-w-md space-y-6">
          <div className="space-y-3">
            <div className="inline-flex items-center gap-2 rounded-full border border-indigo-400/25 bg-indigo-500/10 px-3 py-1 text-[11px] font-semibold uppercase tracking-wider text-indigo-300">
              AI Operations Studio
            </div>
            <h2 className="font-display text-3xl font-bold leading-tight tracking-tight text-white">
              AI-Native Operations, Scoped by Department.
            </h2>
            <p className="text-sm leading-relaxed text-slate-300">
              Connect your team tools, orchestrate visual workflows with AI guardrails, and review
              high-impact actions with human-in-the-loop precision.
            </p>
          </div>

          {/* Mini n8n-style flow preview */}
          <div className="rounded-[var(--radius-xl)] border border-white/10 bg-white/[0.04] p-4 backdrop-blur-md">
            <div className="flex items-center justify-between gap-2 text-xs">
              <div className="flex items-center gap-2 rounded-lg border border-emerald-500/30 bg-emerald-500/10 px-2.5 py-1.5 text-emerald-300 font-medium">
                <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" />
                Trigger
              </div>
              <div className="h-px flex-1 bg-white/15" />
              <div className="flex items-center gap-2 rounded-lg border border-indigo-500/30 bg-indigo-500/15 px-2.5 py-1.5 text-indigo-200 font-medium">
                <Sparkles className="h-3 w-3 text-indigo-400" />
                AI Agent
              </div>
              <div className="h-px flex-1 bg-white/15" />
              <div className="flex items-center gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 px-2.5 py-1.5 text-amber-300 font-medium">
                Approval
              </div>
            </div>
          </div>

          <div className="flex items-center gap-2.5 text-xs text-slate-400 pt-1">
            <ShieldCheck className="h-4 w-4 shrink-0 text-emerald-400" />
            <span>Multi-tenant Row-Level Security &amp; encrypted credentials</span>
          </div>
        </div>

        <p className="relative text-xs text-slate-500">OpsMind Platform · Enterprise Automation</p>
      </aside>

      <main
        className="flex flex-1 flex-col items-center justify-center px-4 py-12 sm:px-8"
        style={{
          backgroundImage: 'radial-gradient(circle at 1px 1px, var(--border) 1px, transparent 0)',
          backgroundSize: '24px 24px',
        }}
      >
        <div className="w-full max-w-md">
          <div className="mb-6 flex flex-col items-center text-center lg:hidden">
            <div className="mb-2.5 flex h-10 w-10 items-center justify-center rounded-[var(--radius-xl)] bg-[var(--primary)] text-[var(--primary-foreground)] shadow-[var(--shadow-sm)]">
              <Sparkles className="h-5 w-5" />
            </div>
            <span className="text-lg font-semibold tracking-tight text-[var(--text)]">OpsMind</span>
          </div>

          <div className="rounded-[var(--radius-2xl)] border border-[var(--border)] bg-[var(--surface)] p-7 shadow-[var(--shadow-lg)]">
            <h1 className="text-lg font-semibold text-[var(--text)]">{title}</h1>
            {subtitle && <p className="mt-1 text-sm text-[var(--text-muted)]">{subtitle}</p>}
            <div className="mt-6 space-y-4">{children}</div>
          </div>

          {footer && (
            <div className="mt-5 text-center text-xs text-[var(--text-muted)]">{footer}</div>
          )}
        </div>
      </main>
    </div>
  );
}
