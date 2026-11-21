import { Link } from 'react-router-dom';
import { Bell, ShieldAlert } from 'lucide-react';
import { useActiveTenant } from '../lib/hooks';
import {
  Badge,
  Button,
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
  PageHeader,
} from '../components/ui';

const NOTIFICATION_ITEMS = [
  {
    id: 'member-invites',
    title: 'Team Member Invitations',
    description: 'Notify when a new member is invited to the organization.',
  },
  {
    id: 'hitl-reviews',
    title: 'Human-in-the-Loop Review Tasks',
    description: 'Notify reviewers when an AI action is placed in the Review Inbox.',
  },
  {
    id: 'workflow-failures',
    title: 'Workflow Execution Failures',
    description: 'Alert admins immediately if an automated workflow step crashes or times out.',
  },
  {
    id: 'connector-expiry',
    title: 'Connector Token Expirations',
    description: 'Alert when a 3rd-party OAuth connection requires re-authentication.',
  },
];

export default function SettingsPage() {
  const { active } = useActiveTenant();

  const canManageSettings = active?.role === 'owner' || active?.role === 'admin';
  if (!canManageSettings) {
    return (
      <div className="mx-auto max-w-xl py-16 text-center space-y-4">
        <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-[var(--radius-xl)] border border-[var(--warning)]/30 bg-[var(--warning-soft)] text-[var(--warning)]">
          <ShieldAlert className="h-6 w-6" />
        </div>
        <h1 className="text-xl font-semibold text-[var(--text)]">Admin Access Required</h1>
        <p className="text-sm text-[var(--text-muted)]">
          Only workspace Owners and Admins can view or modify workspace settings.
        </p>
        <div>
          <Link to="/">
            <Button variant="secondary" size="sm">
              Back to Overview
            </Button>
          </Link>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <PageHeader
        title="Workspace Settings"
        description={
          active
            ? `Manage workspace preferences and operational alert settings for ${active.name}.`
            : 'Manage workspace preferences and operational alert settings.'
        }
        actions={
          <Badge variant="primary">
            <Bell className="h-3 w-3" />
            Preferences
          </Badge>
        }
      />

      <Card>
        <CardHeader className="border-b border-[var(--border)]">
          <CardTitle>Notification Preferences</CardTitle>
          <CardDescription>
            Choose which operational events should trigger alerts for your team.
          </CardDescription>
        </CardHeader>

        <CardContent className="divide-y divide-[var(--border)] pt-2">
          {NOTIFICATION_ITEMS.map((item) => (
            <label
              key={item.id}
              htmlFor={item.id}
              className="flex cursor-pointer items-center justify-between gap-4 py-3.5 text-xs"
            >
              <div>
                <p className="font-semibold text-[var(--text)]">{item.title}</p>
                <p className="mt-0.5 text-[var(--text-muted)]">{item.description}</p>
              </div>
              <input
                id={item.id}
                type="checkbox"
                defaultChecked
                className="h-4 w-4 rounded border-[var(--border)] bg-[var(--surface)] accent-[var(--primary)]"
              />
            </label>
          ))}
        </CardContent>
      </Card>
    </div>
  );
}
