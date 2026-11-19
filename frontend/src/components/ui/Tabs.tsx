import * as RadixTabs from '@radix-ui/react-tabs';
import type { ReactNode } from 'react';

export interface TabsProps {
  value?: string;
  defaultValue?: string;
  onValueChange?: (value: string) => void;
  children: ReactNode;
  className?: string;
}

export function Tabs({ value, defaultValue, onValueChange, children, className = '' }: TabsProps) {
  return (
    <RadixTabs.Root
      value={value}
      defaultValue={defaultValue}
      onValueChange={onValueChange}
      className={className}
    >
      {children}
    </RadixTabs.Root>
  );
}

export function TabsList({ children, className = '' }: { children: ReactNode; className?: string }) {
  return (
    <RadixTabs.List
      className={`inline-flex items-center gap-1 rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface-muted)] p-1 ${className}`}
    >
      {children}
    </RadixTabs.List>
  );
}

export function TabsTrigger({
  value,
  children,
  className = '',
}: {
  value: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <RadixTabs.Trigger
      value={value}
      className={`inline-flex items-center justify-center rounded-[var(--radius-md)] px-3 py-1.5 text-xs font-medium text-[var(--text-muted)] transition-colors cursor-pointer hover:text-[var(--text)] data-[state=active]:bg-[var(--surface)] data-[state=active]:text-[var(--text)] data-[state=active]:shadow-[var(--shadow-xs)] ${className}`}
    >
      {children}
    </RadixTabs.Trigger>
  );
}

export function TabsContent({
  value,
  children,
  className = '',
}: {
  value: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <RadixTabs.Content value={value} className={`mt-4 focus:outline-none ${className}`}>
      {children}
    </RadixTabs.Content>
  );
}
