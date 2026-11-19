import { CalendarClock } from 'lucide-react';
import {
  computeNextRunTimes,
  formatScheduleSummary,
  type ScheduleMode,
  type ScheduleTriggerConfig,
} from '../lib/scheduleUtils';
import { Input, Select } from './ui';

const WEEKDAYS = [
  { value: 0, label: 'Mon' },
  { value: 1, label: 'Tue' },
  { value: 2, label: 'Wed' },
  { value: 3, label: 'Thu' },
  { value: 4, label: 'Fri' },
  { value: 5, label: 'Sat' },
  { value: 6, label: 'Sun' },
];

export interface ScheduleEditorProps {
  value: ScheduleTriggerConfig;
  timezone: string;
  onChange: (next: ScheduleTriggerConfig) => void;
  disabled?: boolean;
}

export function ScheduleEditor({ value, timezone, onChange, disabled }: ScheduleEditorProps) {
  const summary = formatScheduleSummary(value, timezone);
  const nextRuns = computeNextRunTimes(value, timezone, 3);
  const selectedDays = value.weekdays ?? [0];

  const toggleWeekday = (day: number) => {
    const exists = selectedDays.includes(day);
    const next = exists
      ? selectedDays.filter((d) => d !== day)
      : [...selectedDays, day].sort((a, b) => a - b);
    if (next.length > 0) {
      onChange({ ...value, weekdays: next });
    }
  };

  return (
    <div className="space-y-3">
      <Select
        label="Schedule Mode"
        disabled={disabled}
        value={value.mode}
        onChange={(e) => {
          const mode = e.target.value as ScheduleMode;
          onChange({
            type: 'schedule',
            mode,
            time: mode === 'daily' || mode === 'weekly' ? value.time || '09:00' : null,
            weekdays: mode === 'weekly' ? value.weekdays ?? [0] : null,
            every_minutes: mode === 'interval' ? value.every_minutes ?? 15 : null,
            cron: mode === 'cron' ? value.cron || '0 9 * * *' : null,
          });
        }}
      >
        <option value="daily">Daily</option>
        <option value="weekly">Weekly</option>
        <option value="interval">Interval</option>
        <option value="cron">Cron</option>
      </Select>

      {(value.mode === 'daily' || value.mode === 'weekly') && (
        <Input
          label={`Time (${timezone})`}
          type="time"
          disabled={disabled}
          value={value.time || '09:00'}
          onChange={(e) => onChange({ ...value, time: e.target.value })}
        />
      )}

      {value.mode === 'weekly' && (
        <div className="space-y-1.5">
          <span className="block text-xs font-medium text-[var(--text-muted)]">Weekdays</span>
          <div className="flex flex-wrap gap-1.5">
            {WEEKDAYS.map((day) => {
              const active = selectedDays.includes(day.value);
              return (
                <button
                  key={day.value}
                  type="button"
                  disabled={disabled}
                  aria-pressed={active}
                  onClick={() => toggleWeekday(day.value)}
                  className={`rounded-[var(--radius-md)] border px-2.5 py-1 text-xs font-medium transition-colors cursor-pointer ${
                    active
                      ? 'border-[var(--primary)] bg-[var(--primary-soft)] text-[var(--primary)]'
                      : 'border-[var(--border)] bg-[var(--surface)] text-[var(--text-muted)] hover:border-[var(--border-hover)]'
                  }`}
                >
                  {day.label}
                </button>
              );
            })}
          </div>
        </div>
      )}

      {value.mode === 'interval' && (
        <Input
          label="Every (minutes, 5–10080)"
          type="number"
          min={5}
          max={10080}
          disabled={disabled}
          value={value.every_minutes ?? 15}
          onChange={(e) => onChange({ ...value, every_minutes: Number(e.target.value) })}
        />
      )}

      {value.mode === 'cron' && (
        <Input
          label="Cron Expression (5-field)"
          disabled={disabled}
          value={value.cron ?? '0 9 * * *'}
          placeholder="*/15 9-17 * * 1-5"
          onChange={(e) => onChange({ ...value, cron: e.target.value })}
        />
      )}

      <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface-muted)] p-3 text-xs space-y-1.5">
        <div className="flex items-center gap-1.5 font-medium text-[var(--text)]">
          <CalendarClock className="h-3.5 w-3.5 text-[var(--primary)]" />
          <span>{summary}</span>
        </div>
        {nextRuns.length > 0 && (
          <div className="text-[11px] text-[var(--text-muted)]">
            <span className="font-medium">Next 3 runs:</span> {nextRuns.join(' • ')}
          </div>
        )}
      </div>
    </div>
  );
}
