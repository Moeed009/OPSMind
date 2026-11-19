import { Building2, Check, ChevronDown } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';
import { useActiveTenant } from '../lib/hooks';
import { setTenantId } from '../lib/tenant';
import { Badge } from './ui';

export default function TenantSwitcher() {
  const { tenants, active } = useActiveTenant();
  const [open, setOpen] = useState(false);
  const dropdownRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    document.addEventListener('mousedown', handleClickOutside);
    return () => document.removeEventListener('mousedown', handleClickOutside);
  }, []);

  if (!active) {
    return (
      <div className="flex items-center gap-2 rounded-[var(--radius-md)] border border-dashed border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5 text-xs text-[var(--text-muted)]">
        <Building2 className="h-3.5 w-3.5 text-[var(--text-subtle)]" />
        <span>No workspace</span>
      </div>
    );
  }

  const planVariant =
    active.plan.toLowerCase() === 'pro'
      ? 'primary'
      : active.plan.toLowerCase() === 'starter'
        ? 'sky'
        : 'neutral';

  if (tenants.length <= 1) {
    return (
      <div className="flex items-center gap-2 rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5 text-xs font-medium text-[var(--text)] shadow-[var(--shadow-xs)]">
        <div className="flex h-5 w-5 items-center justify-center rounded-[var(--radius-sm)] bg-[var(--primary-soft)] text-[var(--primary)]">
          <Building2 className="h-3 w-3" />
        </div>
        <span className="max-w-[140px] truncate font-semibold sm:max-w-[180px]">
          {active.name}
        </span>
        <Badge variant={planVariant} size="xs" className="uppercase">
          {active.plan}
        </Badge>
      </div>
    );
  }

  return (
    <div className="relative inline-flex items-center" ref={dropdownRef}>
      <button
        type="button"
        onClick={() => setOpen((prev) => !prev)}
        aria-expanded={open}
        aria-haspopup="listbox"
        className="flex items-center gap-2 rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface)] px-2.5 py-1.5 text-xs font-medium text-[var(--text)] shadow-[var(--shadow-xs)] transition-colors hover:border-[var(--border-hover)] hover:bg-[var(--surface-muted)] cursor-pointer"
      >
        <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-[var(--radius-sm)] bg-[var(--primary-soft)] text-[var(--primary)]">
          <Building2 className="h-3 w-3" />
        </div>
        <span className="max-w-[130px] truncate font-semibold sm:max-w-[170px]">
          {active.name}
        </span>
        <Badge variant={planVariant} size="xs" className="uppercase">
          {active.plan}
        </Badge>
        <ChevronDown className="h-3.5 w-3.5 text-[var(--text-subtle)]" />
      </button>

      {open && (
        <div
          role="listbox"
          className="absolute left-0 top-full z-50 mt-1.5 w-56 rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface)] p-1.5 shadow-[var(--shadow-md)]"
        >
          <div className="px-2.5 py-1.5 text-[10px] font-semibold uppercase tracking-wider text-[var(--text-subtle)]">
            Switch Organization
          </div>
          <div className="space-y-0.5">
            {tenants.map((t) => {
              const isSelected = t.id === active.id;
              return (
                <button
                  key={t.id}
                  type="button"
                  role="option"
                  aria-selected={isSelected}
                  onClick={() => {
                    setTenantId(t.id);
                    setOpen(false);
                  }}
                  className={`flex w-full items-center justify-between rounded-[var(--radius-md)] px-2.5 py-1.5 text-xs font-medium transition-colors cursor-pointer ${
                    isSelected
                      ? 'bg-[var(--primary-soft)] text-[var(--primary)] font-semibold'
                      : 'text-[var(--text-muted)] hover:bg-[var(--surface-muted)] hover:text-[var(--text)]'
                  }`}
                >
                  <div className="flex items-center gap-2 truncate">
                    <Building2 className="h-3.5 w-3.5 shrink-0" />
                    <span className="truncate">{t.name}</span>
                  </div>
                  {isSelected && <Check className="h-3.5 w-3.5 shrink-0" />}
                </button>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}
