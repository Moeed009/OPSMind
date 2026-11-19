import { memo, type ComponentType } from 'react';
import { Handle, Position, type NodeProps } from '@xyflow/react';
import {
  Bot,
  Clock,
  FileSpreadsheet,
  Filter,
  GitBranch,
  Search,
  Send,
  ShieldAlert,
  Zap,
} from 'lucide-react';

const STEP_ICONS: Record<string, ComponentType<{ className?: string }>> = {
  trigger: Zap,
  classify: Filter,
  retrieve: Search,
  extract: FileSpreadsheet,
  agent: Bot,
  condition: GitBranch,
  approval: ShieldAlert,
  action: Send,
  wait: Clock,
};

interface NodeTheme {
  accentBar: string;
  iconBox: string;
  badgeText: string;
}

const STEP_THEMES: Record<string, NodeTheme> = {
  trigger: {
    accentBar: 'bg-emerald-500',
    iconBox: 'bg-emerald-500/15 text-emerald-500 border-emerald-500/30',
    badgeText: 'text-emerald-500',
  },
  classify: {
    accentBar: 'bg-violet-500',
    iconBox: 'bg-violet-500/15 text-violet-400 border-violet-500/30',
    badgeText: 'text-violet-400',
  },
  retrieve: {
    accentBar: 'bg-indigo-500',
    iconBox: 'bg-indigo-500/15 text-indigo-400 border-indigo-500/30',
    badgeText: 'text-indigo-400',
  },
  extract: {
    accentBar: 'bg-purple-500',
    iconBox: 'bg-purple-500/15 text-purple-400 border-purple-500/30',
    badgeText: 'text-purple-400',
  },
  agent: {
    accentBar: 'bg-[var(--primary)]',
    iconBox: 'bg-[var(--primary-soft)] text-[var(--primary)] border-[var(--primary)]/30',
    badgeText: 'text-[var(--primary)]',
  },
  ai: {
    accentBar: 'bg-violet-500',
    iconBox: 'bg-violet-500/15 text-violet-400 border-violet-500/30',
    badgeText: 'text-violet-400',
  },
  condition: {
    accentBar: 'bg-amber-500',
    iconBox: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
    badgeText: 'text-amber-400',
  },
  approval: {
    accentBar: 'bg-rose-500',
    iconBox: 'bg-rose-500/15 text-rose-400 border-rose-500/30',
    badgeText: 'text-rose-400',
  },
  action: {
    accentBar: 'bg-sky-500',
    iconBox: 'bg-sky-500/15 text-sky-400 border-sky-500/30',
    badgeText: 'text-sky-400',
  },
  wait: {
    accentBar: 'bg-slate-400',
    iconBox: 'bg-[var(--surface-muted)] text-[var(--text-muted)] border-[var(--border)]',
    badgeText: 'text-[var(--text-muted)]',
  },
};

function getNodeSubtitle(type: string, data: Record<string, unknown>): string | null {
  const config = (data.config as Record<string, unknown> | undefined) ?? data;
  if (type === 'action' && typeof config.action === 'string' && config.action) {
    return config.action;
  }
  if (type === 'condition' && typeof config.expression === 'string' && config.expression) {
    return config.expression;
  }
  if (type === 'approval') {
    if (typeof config.title === 'string' && config.title) return config.title;
    if (typeof config.role === 'string' && config.role) return `role: ${config.role}`;
  }
  if (
    (type === 'ai' || type === 'classify' || type === 'extract' || type === 'retrieve' || type === 'agent') &&
    typeof config.task === 'string' &&
    config.task
  ) {
    return config.task;
  }
  return null;
}

export const CustomWorkflowNode = memo(({ data, selected }: NodeProps) => {
  const type = String(data.type || 'agent');
  const Icon = STEP_ICONS[type] || Bot;
  const theme = STEP_THEMES[type] || STEP_THEMES.wait;
  const isTrigger = type === 'trigger';
  const isCondition = type === 'condition';
  const subtitle = getNodeSubtitle(type, data as Record<string, unknown>);

  return (
    <div
      className={`group relative min-w-[235px] max-w-[265px] overflow-hidden rounded-[var(--radius-xl)] border bg-[var(--surface)] shadow-[var(--shadow-sm)] transition-all duration-150 select-none ${
        selected
          ? 'border-[var(--primary)] ring-2 ring-[var(--primary-glow)] shadow-[var(--shadow-md)]'
          : 'border-[var(--border)] hover:border-[var(--border-hover)] hover:shadow-[var(--shadow-md)]'
      }`}
    >
      {/* n8n-style colored left accent stripe */}
      <div className={`absolute inset-y-0 left-0 w-1 ${theme.accentBar}`} />

      {!isTrigger && (
        <Handle
          type="target"
          position={Position.Left}
          className="!-left-1.5 !h-3 !w-3 !rounded-full !border-2 !border-[var(--surface)] !bg-[var(--text-muted)] transition-transform hover:!scale-125"
        />
      )}

      <div className="flex items-start gap-3 py-3 pl-4 pr-3.5">
        <div
          className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-[var(--radius-lg)] border ${theme.iconBox}`}
        >
          <Icon className="h-4 w-4" />
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-2">
            <span
              className={`font-mono text-[10px] font-bold uppercase tracking-wider ${theme.badgeText}`}
            >
              {type}
            </span>
          </div>
          <div className="mt-0.5 truncate text-[13px] font-semibold text-[var(--text)]">
            {String(data.label || 'Step')}
          </div>
          {subtitle && (
            <div className="mt-1.5 truncate rounded-[var(--radius-sm)] bg-[var(--surface-muted)] px-1.5 py-0.5 font-mono text-[10px] text-[var(--text-muted)]">
              {subtitle}
            </div>
          )}
        </div>
      </div>

      {isCondition ? (
        <>
          <div className="border-t border-[var(--border)] bg-[var(--surface-muted)]/40 px-3.5 py-1.5 flex items-center justify-between text-[10px] font-mono font-semibold">
            <span className="text-emerald-500">TRUE ↗</span>
            <span className="text-rose-500">FALSE ↘</span>
          </div>
          <Handle
            id="true"
            type="source"
            position={Position.Right}
            style={{ top: '34%' }}
            className="!-right-1.5 !h-3 !w-3 !rounded-full !border-2 !border-[var(--surface)] !bg-emerald-500 transition-transform hover:!scale-125"
          />
          <Handle
            id="false"
            type="source"
            position={Position.Right}
            style={{ top: '68%' }}
            className="!-right-1.5 !h-3 !w-3 !rounded-full !border-2 !border-[var(--surface)] !bg-rose-500 transition-transform hover:!scale-125"
          />
        </>
      ) : (
        <Handle
          type="source"
          position={Position.Right}
          className="!-right-1.5 !h-3 !w-3 !rounded-full !border-2 !border-[var(--surface)] !bg-[var(--primary)] transition-transform hover:!scale-125"
        />
      )}
    </div>
  );
});
