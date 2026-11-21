export type ScheduleMode = 'daily' | 'weekly' | 'interval' | 'cron';

export interface ScheduleTriggerConfig {
  type: 'schedule';
  mode: ScheduleMode;
  time?: string | null;
  weekdays?: number[] | null;
  every_minutes?: number | null;
  cron?: string | null;
}

export interface EventTriggerConfig {
  type: 'event';
  source: string;
}

export interface ManualTriggerConfig {
  type: 'manual';
}

export type DeptTriggerConfig = ScheduleTriggerConfig | EventTriggerConfig | ManualTriggerConfig;

export interface DeptWorkflowNode {
  id: string;
  type: 'classify' | 'retrieve' | 'extract' | 'agent' | 'condition' | 'approval' | 'action' | 'wait';
  label?: string | null;
  prompt?: string | null;
  system_prompt?: string | null;
  labels?: string[] | null;
  output_schema?: Record<string, unknown> | null;
  confidence_threshold?: number;
  provider?: 'auto' | 'groq' | 'gemini';
  model?: string | null;
  query_template?: string;
  top_k?: number;
  include_company_shared?: boolean;
  mode?: 'knowledge' | 'aggregate_ops';
  require_sources?: boolean;
  expression?: string;
  role?: 'reviewer' | 'admin';
  timeout_hours?: number | null;
  action?: string;
  params?: Record<string, unknown>;
  wait_seconds?: number;
  until_expression?: string | null;
}

export interface DeptWorkflowEdge {
  source: string;
  target: string;
  label?: 'true' | 'false' | null;
}

export interface DeptWorkflowDefinition {
  trigger: DeptTriggerConfig;
  nodes: DeptWorkflowNode[];
  edges: DeptWorkflowEdge[];
}

const WEEKDAY_NAMES = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
const WEEKDAY_MAP: Record<string, number> = {
  Mon: 0,
  Tue: 1,
  Wed: 2,
  Thu: 3,
  Fri: 4,
  Sat: 5,
  Sun: 6,
};

export function formatScheduleSummary(trigger: ScheduleTriggerConfig, timezone: string): string {
  if (trigger.mode === 'daily') {
    return `Every day at ${trigger.time || '09:00'} (${timezone})`;
  }
  if (trigger.mode === 'weekly') {
    const days = (trigger.weekdays ?? [0])
      .slice()
      .sort((a, b) => a - b)
      .map((d) => WEEKDAY_NAMES[d] ?? String(d))
      .join(', ');
    return `Weekly on ${days || 'Mon'} at ${trigger.time || '09:00'} (${timezone})`;
  }
  if (trigger.mode === 'interval') {
    return `Every ${trigger.every_minutes ?? 15} minutes (${timezone})`;
  }
  return `Cron "${trigger.cron || '0 9 * * *'}" (${timezone})`;
}

function makeLocalPartsGetter(timezone: string) {
  const fmt = new Intl.DateTimeFormat('en-US', {
    timeZone: timezone,
    weekday: 'short',
    year: 'numeric',
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });
  return (date: Date) => {
    const parts = fmt.formatToParts(date);
    const get = (type: string) => parts.find((p) => p.type === type)?.value ?? '';
    const hourRaw = Number(get('hour'));
    return {
      weekday: WEEKDAY_MAP[get('weekday')] ?? 0,
      month: Number(get('month')),
      day: Number(get('day')),
      hour: hourRaw === 24 ? 0 : hourRaw,
      minute: Number(get('minute')),
    };
  };
}

function cronFieldMatches(token: string, val: number): boolean {
  for (const part of token.split(',')) {
    const [base, stepStr] = part.split('/');
    const step = stepStr ? Number(stepStr) : 1;
    if (!Number.isFinite(step) || step <= 0) return false;
    if (base === '*') {
      if (val % step === 0) return true;
    } else if (base.includes('-')) {
      const [loS, hiS] = base.split('-');
      const lo = Number(loS);
      const hi = Number(hiS);
      if (val >= lo && val <= hi && (val - lo) % step === 0) return true;
    } else if (Number(base) === val) {
      return true;
    }
  }
  return false;
}

export function computeNextRunTimes(
  trigger: ScheduleTriggerConfig,
  timezone: string,
  count = 3,
  fromDate = new Date(),
): string[] {
  const results: string[] = [];
  const startMs = Math.ceil(fromDate.getTime() / 60_000) * 60_000;
  const maxSteps = trigger.mode === 'interval' ? 60 * 24 * 14 : 60 * 24 * 31;
  const stepMinutes = trigger.mode === 'interval' ? 1 : 1;

  const [targetH, targetM] = (trigger.time || '09:00').split(':').map(Number);
  const cronParts = (trigger.cron || '').trim().split(/\s+/);
  const getLocalParts = makeLocalPartsGetter(timezone);
  const outFmt = new Intl.DateTimeFormat('en-US', {
    timeZone: timezone,
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  });

  for (let i = 0; i < maxSteps && results.length < count; i += stepMinutes) {
    const cand = new Date(startMs + i * 60_000);
    let matches = false;

    if (trigger.mode === 'interval') {
      const every = trigger.every_minutes ?? 15;
      if (every >= 5 && Math.floor(cand.getTime() / 60_000) % every === 0) {
        matches = true;
      }
    } else {
      const local = getLocalParts(cand);
      if (trigger.mode === 'daily') {
        matches = local.hour === targetH && local.minute === targetM;
      } else if (trigger.mode === 'weekly') {
        const wds = trigger.weekdays ?? [];
        matches = wds.includes(local.weekday) && local.hour === targetH && local.minute === targetM;
      } else if (trigger.mode === 'cron' && cronParts.length === 5) {
        const [mF, hF, domF, monF, dowF] = cronParts;
        const cronDow = (local.weekday + 1) % 7;
        matches =
          cronFieldMatches(mF, local.minute) &&
          cronFieldMatches(hF, local.hour) &&
          cronFieldMatches(domF, local.day) &&
          cronFieldMatches(monF, local.month) &&
          (cronFieldMatches(dowF, cronDow) || (cronDow === 0 && cronFieldMatches(dowF, 7)));
      }
    }

    if (matches) {
      results.push(outFmt.format(cand));
    }
  }

  return results;
}

export function validateNodeConfig(
  node: DeptWorkflowNode,
  edges: DeptWorkflowEdge[],
): string[] {
  const errs: string[] = [];
  if (!node.id.trim()) errs.push('Node ID is required.');
  if (node.type === 'classify' || node.type === 'extract' || node.type === 'agent') {
    if (
      node.confidence_threshold !== undefined &&
      (node.confidence_threshold < 0 || node.confidence_threshold > 1)
    ) {
      errs.push('Confidence threshold must be between 0.0 and 1.0.');
    }
    if (node.type === 'classify' && node.labels && node.labels.length === 0) {
      errs.push('Classify node should specify at least one label.');
    }
  } else if (node.type === 'retrieve') {
    if (node.top_k !== undefined && (node.top_k < 1 || node.top_k > 20)) {
      errs.push('Top K must be between 1 and 20.');
    }
  } else if (node.type === 'wait') {
    if (node.wait_seconds !== undefined && (node.wait_seconds < 0 || node.wait_seconds > 86400)) {
      errs.push('Wait seconds must be between 0 and 86400.');
    }
  } else if (node.type === 'condition') {
    if (!node.expression || !node.expression.trim()) {
      errs.push('Condition expression is required.');
    }
    const outgoing = edges.filter((e) => e.source === node.id).map((e) => e.label);
    if (!outgoing.includes('true') || !outgoing.includes('false')) {
      errs.push('Condition node must have both "true" and "false" outgoing branches.');
    }
  } else if (node.type === 'action') {
    if (!node.action || !node.action.trim()) {
      errs.push('Action key is required.');
    }
  } else if (node.type === 'approval') {
    if (
      node.timeout_hours !== null &&
      node.timeout_hours !== undefined &&
      (node.timeout_hours < 1 || node.timeout_hours > 720)
    ) {
      errs.push('Timeout hours must be between 1 and 720.');
    }
  }
  return errs;
}
