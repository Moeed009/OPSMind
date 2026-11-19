import { useMemo, useState, type FormEvent } from 'react';
import {
  AlertTriangle,
  Building2,
  Link2,
  Mail,
  Plug,
  RefreshCw,
  Trash2,
  Unplug,
} from 'lucide-react';
import { useActiveTenant } from '../lib/hooks';
import { useDepartments, type Department } from '../lib/departmentsApi';
import {
  useAuthorize,
  useConnections,
  useDeleteConnection,
  useDirectConnect,
  usePoll,
  useRevoke,
  type Connection,
  type ConnectionStatus,
} from '../lib/connectionsApi';
import { getErrorMessage } from '../lib/api';
import {
  Alert,
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  ConfirmDialog,
  Dialog,
  EmptyState,
  ErrorState,
  Input,
  PageHeader,
  Select,
  Skeleton,
  useToast,
} from '../components/ui';

type ConnectMode = 'oauth' | 'direct';

const PROVIDERS = [
  { value: 'gmail', label: 'Gmail' },
  { value: 'google_drive', label: 'Google Drive' },
  { value: 'google_calendar', label: 'Google Calendar' },
  { value: 'google_sheets', label: 'Google Sheets' },
  { value: 'slack', label: 'Slack' },
  { value: 'hubspot', label: 'HubSpot (CRM & Tickets)' },
  { value: 'stripe', label: 'Stripe' },
  { value: 'zoho_books', label: 'Zoho Books' },
];

function formatDate(iso: string): string {
  return new Date(iso).toLocaleDateString();
}

function statusVariant(status: ConnectionStatus): 'success' | 'danger' | 'warning' | 'neutral' {
  switch (status) {
    case 'CONNECTED':
      return 'success';
    case 'ERROR':
    case 'NEEDS_REAUTH':
      return 'danger';
    case 'RATE_LIMITED':
      return 'warning';
    case 'REVOKED':
      return 'neutral';
  }
}

export function ConnectionsPage() {
  const { active } = useActiveTenant();
  const { toast } = useToast();
  const role = active?.role;
  const isCompanyAdmin = role === 'owner' || role === 'admin';

  const [deptFilter, setDeptFilter] = useState<string>('all');
  const departmentsQuery = useDepartments();
  const connectionsQuery = useConnections();

  const authorize = useAuthorize();
  const directConnect = useDirectConnect();
  const poll = usePoll();
  const revoke = useRevoke();
  const deleteConnection = useDeleteConnection();

  const [connectOpen, setConnectOpen] = useState(false);
  const [connectMode, setConnectMode] = useState<ConnectMode>('direct');
  const [provider, setProvider] = useState('gmail');
  const [departmentId, setDepartmentId] = useState('');
  const [externalAccountId, setExternalAccountId] = useState('');
  const [accessToken, setAccessToken] = useState('');
  const [connectError, setConnectError] = useState<string | null>(null);
  const [isReassign, setIsReassign] = useState(false);

  const [confirmDisconnect, setConfirmDisconnect] = useState<Connection | null>(null);
  const [confirmDelete, setConfirmDelete] = useState<Connection | null>(null);

  const departments = useMemo(() => departmentsQuery.data ?? [], [departmentsQuery.data]);
  const connections = useMemo(() => connectionsQuery.data ?? [], [connectionsQuery.data]);

  const deptById = useMemo(() => {
    const map = new Map<string, Department>();
    for (const d of departments) map.set(d.id, d);
    return map;
  }, [departments]);

  const adminDepts = useMemo(
    () => departments.filter((d) => isCompanyAdmin || d.my_effective_role === 'admin'),
    [departments, isCompanyAdmin],
  );

  const canConnectAny = isCompanyAdmin || adminDepts.length > 0;

  function canManageConnection(conn: Connection): boolean {
    if (isCompanyAdmin) return true;
    if (!conn.department_id) return false;
    return deptById.get(conn.department_id)?.my_effective_role === 'admin';
  }

  function openConnectModal(prefill?: Connection) {
    setConnectError(null);
    if (prefill) {
      setIsReassign(true);
      setProvider(prefill.provider);
      setExternalAccountId(prefill.external_account_id ?? '');
      setDepartmentId(adminDepts[0]?.id ?? departments[0]?.id ?? '');
      setConnectMode('direct');
    } else {
      setIsReassign(false);
      setProvider('gmail');
      setExternalAccountId('');
      setDepartmentId(
        deptFilter !== 'all' && deptFilter !== 'unassigned'
          ? deptFilter
          : (adminDepts[0]?.id ?? departments[0]?.id ?? ''),
      );
      setConnectMode('direct');
    }
    setAccessToken('');
    setConnectOpen(true);
  }

  async function handleConnectSubmit(e: FormEvent) {
    e.preventDefault();
    setConnectError(null);
    const targetDeptId = departmentId || undefined;
    try {
      if (connectMode === 'oauth') {
        const res = await authorize.mutateAsync({
          provider,
          department_id: targetDeptId,
        });
        window.open(res.authorize_url, '_blank', 'noopener,noreferrer');
        toast({
          variant: 'default',
          title: 'Authorization started',
          description: `Complete ${provider} OAuth in the opened window.`,
        });
        setConnectOpen(false);
        return;
      }

      await directConnect.mutateAsync({
        provider,
        external_account_id: externalAccountId.trim(),
        credentials: accessToken.trim() ? { access_token: accessToken.trim() } : {},
        department_id: targetDeptId,
      });
      toast({
        variant: 'success',
        title: isReassign ? 'Department assigned' : 'Service connected',
        description: `${provider} (${externalAccountId.trim()}) is now connected.`,
      });
      setConnectOpen(false);
    } catch (err) {
      setConnectError(getErrorMessage(err));
    }
  }

  async function handlePoll(conn: Connection) {
    try {
      const res = await poll.mutateAsync(conn.id);
      toast({
        variant: 'success',
        title: 'Poll complete',
        description:
          res.inserted > 0
            ? `Ingested ${res.inserted} new event(s).`
            : 'No new events found.',
      });
    } catch (err) {
      toast({
        variant: 'danger',
        title: 'Poll failed',
        description: getErrorMessage(err),
      });
    }
  }

  async function handleConfirmDisconnect() {
    if (!confirmDisconnect) return;
    try {
      const res = await revoke.mutateAsync(confirmDisconnect.id);
      const pausedCount = res.paused_workflows?.length ?? 0;
      toast({
        variant: pausedCount > 0 ? 'warning' : 'success',
        title: 'Connection disconnected',
        description:
          pausedCount > 0
            ? `${pausedCount} dependent workflow(s) were paused. Resume them manually after reconnecting.`
            : `${confirmDisconnect.external_account_id} was disconnected.`,
      });
      setConfirmDisconnect(null);
    } catch (err) {
      toast({
        variant: 'danger',
        title: 'Disconnect failed',
        description: getErrorMessage(err),
      });
    }
  }

  async function handleConfirmDelete() {
    if (!confirmDelete) return;
    try {
      const res = await deleteConnection.mutateAsync(confirmDelete.id);
      const pausedCount = res.paused_workflows?.length ?? 0;
      toast({
        variant: pausedCount > 0 ? 'warning' : 'success',
        title: 'Connection deleted',
        description:
          pausedCount > 0
            ? `${pausedCount} dependent workflow(s) were paused.`
            : `${confirmDelete.external_account_id} was permanently deleted.`,
      });
      setConfirmDelete(null);
    } catch (err) {
      toast({
        variant: 'danger',
        title: 'Delete failed',
        description: getErrorMessage(err),
      });
    }
  }

  const grouped = useMemo(() => {
    const unassigned: Connection[] = [];
    const byDept = new Map<string, Connection[]>();

    for (const c of connections) {
      if (!c.department_id) {
        unassigned.push(c);
      } else {
        const list = byDept.get(c.department_id) ?? [];
        list.push(c);
        byDept.set(c.department_id, list);
      }
    }

    const sections: {
      key: string;
      title: string;
      subtitle?: string;
      isUnassigned: boolean;
      items: Connection[];
    }[] = [];

    if (unassigned.length > 0 && (deptFilter === 'all' || deptFilter === 'unassigned')) {
      sections.push({
        key: 'unassigned',
        title: 'Unassigned',
        subtitle: 'Legacy connections without a department cannot be used by department workflows.',
        isUnassigned: true,
        items: unassigned,
      });
    }

    for (const [dId, items] of byDept.entries()) {
      if (deptFilter !== 'all' && deptFilter !== dId) continue;
      const d = deptById.get(dId);
      sections.push({
        key: dId,
        title: d ? d.name : 'Department',
        subtitle: d ? d.code : dId,
        isUnassigned: false,
        items,
      });
    }

    return sections;
  }, [connections, deptById, deptFilter]);

  return (
    <div className="space-y-6">
      <PageHeader
        title="Connections"
        description="Connect mailboxes and messaging accounts to specific departments. One mailbox belongs to one department."
        actions={
          canConnectAny ? (
            <Button variant="primary" size="sm" onClick={() => openConnectModal()}>
              <Plug className="h-4 w-4" />
              Connect Service
            </Button>
          ) : undefined
        }
      />

      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="w-full sm:w-64">
          <Select
            aria-label="Filter by department"
            value={deptFilter}
            onChange={(e) => setDeptFilter(e.target.value)}
          >
            <option value="all">All departments</option>
            <option value="unassigned">Unassigned</option>
            {departments.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name} ({d.code})
              </option>
            ))}
          </Select>
        </div>
      </div>

      {connectionsQuery.isLoading ? (
        <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
          <Skeleton className="h-44 w-full" />
          <Skeleton className="h-44 w-full" />
          <Skeleton className="h-44 w-full" />
        </div>
      ) : connectionsQuery.isError ? (
        <ErrorState
          title="Could not load connections"
          message={getErrorMessage(connectionsQuery.error)}
          onRetry={() => void connectionsQuery.refetch()}
        />
      ) : grouped.length === 0 ? (
        <EmptyState
          icon={Link2}
          title="No connections found"
          description="Connect a department mailbox or messaging account to trigger workflows and dispatch actions."
          action={
            canConnectAny ? (
              <Button variant="primary" size="sm" onClick={() => openConnectModal()}>
                <Plug className="h-4 w-4" />
                Connect Service
              </Button>
            ) : undefined
          }
        />
      ) : (
        <div className="space-y-6">
          {grouped.map((section) => (
            <section key={section.key} className="space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="flex items-center gap-2">
                  {section.isUnassigned ? (
                    <AlertTriangle className="h-4 w-4 text-[var(--warning)]" />
                  ) : (
                    <Building2 className="h-4 w-4 text-[var(--text-muted)]" />
                  )}
                  <h2 className="text-sm font-semibold text-[var(--text)]">{section.title}</h2>
                  {section.isUnassigned ? (
                    <Badge variant="warning">Unassigned</Badge>
                  ) : section.subtitle ? (
                    <Badge variant="neutral">{section.subtitle}</Badge>
                  ) : null}
                </div>
                {section.subtitle && section.isUnassigned && (
                  <p className="text-xs text-[var(--text-muted)]">{section.subtitle}</p>
                )}
              </div>

              <div className="grid gap-4 md:grid-cols-2 lg:grid-cols-3">
                {section.items.map((conn) => {
                  const canManage = canManageConnection(conn);
                  return (
                    <Card key={conn.id} className="flex flex-col justify-between">
                      <CardHeader>
                        <div className="flex items-start justify-between gap-2">
                          <div className="flex min-w-0 items-center gap-2.5">
                            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-[var(--radius-md)] bg-[var(--surface-muted)] text-[var(--primary)]">
                              <Mail className="h-4 w-4" />
                            </div>
                            <div className="min-w-0">
                              <p className="truncate text-sm font-semibold text-[var(--text)] capitalize">
                                {conn.provider}
                              </p>
                              <p className="truncate text-xs text-[var(--text-muted)]">
                                {conn.external_account_id}
                              </p>
                            </div>
                          </div>
                          <Badge variant={statusVariant(conn.status)}>{conn.status}</Badge>
                        </div>
                      </CardHeader>
                      <CardContent className="space-y-3">
                        <div className="flex items-center justify-between text-xs text-[var(--text-muted)]">
                          <span>Last synced</span>
                          <span>
                            {conn.last_synced_at ? formatDate(conn.last_synced_at) : 'Never'}
                          </span>
                        </div>

                        {canManage && (
                          <div className="flex flex-wrap items-center gap-2 pt-1">
                            {section.isUnassigned && (
                              <Button
                                type="button"
                                variant="primary"
                                size="sm"
                                onClick={() => openConnectModal(conn)}
                              >
                                Assign department
                              </Button>
                            )}
                            {conn.status === 'CONNECTED' && (
                              <Button
                                type="button"
                                variant="secondary"
                                size="sm"
                                loading={poll.isPending}
                                onClick={() => void handlePoll(conn)}
                              >
                                <RefreshCw className="h-3.5 w-3.5" />
                                Poll
                              </Button>
                            )}
                            {conn.status !== 'REVOKED' && (
                              <Button
                                type="button"
                                variant="secondary"
                                size="sm"
                                onClick={() => setConfirmDisconnect(conn)}
                              >
                                <Unplug className="h-3.5 w-3.5" />
                                Disconnect
                              </Button>
                            )}
                            <Button
                              type="button"
                              variant="ghost"
                              size="sm"
                              aria-label={`Delete ${conn.external_account_id}`}
                              onClick={() => setConfirmDelete(conn)}
                            >
                              <Trash2 className="h-3.5 w-3.5 text-[var(--danger)]" />
                            </Button>
                          </div>
                        )}
                      </CardContent>
                    </Card>
                  );
                })}
              </div>
            </section>
          ))}
        </div>
      )}

      <Dialog
        open={connectOpen}
        onOpenChange={setConnectOpen}
        title={isReassign ? 'Assign connection to department' : 'Connect Service'}
        description="Bind an account to a single department. Attaching an account already bound elsewhere will be rejected."
      >
        <form onSubmit={(e) => void handleConnectSubmit(e)} className="space-y-4">
          {connectError && <Alert kind="error">{connectError}</Alert>}

          {!isReassign && (
            <div className="flex gap-2">
              <Button
                type="button"
                size="sm"
                variant={connectMode === 'direct' ? 'primary' : 'secondary'}
                onClick={() => setConnectMode('direct')}
              >
                Direct connect
              </Button>
              <Button
                type="button"
                size="sm"
                variant={connectMode === 'oauth' ? 'primary' : 'secondary'}
                onClick={() => setConnectMode('oauth')}
              >
                OAuth
              </Button>
            </div>
          )}

          <Select
            label="Department"
            value={departmentId}
            onChange={(e) => setDepartmentId(e.target.value)}
            required
          >
            {adminDepts.map((d) => (
              <option key={d.id} value={d.id}>
                {d.name} ({d.code})
              </option>
            ))}
          </Select>

          <Select
            label="Provider"
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
            disabled={isReassign}
          >
            {PROVIDERS.map((p) => (
              <option key={p.value} value={p.value}>
                {p.label}
              </option>
            ))}
          </Select>

          {connectMode === 'direct' && (
            <>
              <Input
                label="Account / Mailbox"
                required
                placeholder="support@company.com"
                value={externalAccountId}
                onChange={(e) => setExternalAccountId(e.target.value)}
                disabled={isReassign}
              />
              <Input
                label="Access token (optional for test/dev)"
                type="password"
                placeholder="Paste OAuth or API token"
                value={accessToken}
                onChange={(e) => setAccessToken(e.target.value)}
              />
            </>
          )}

          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" size="sm" onClick={() => setConnectOpen(false)}>
              Cancel
            </Button>
            <Button
              type="submit"
              variant="primary"
              size="sm"
              loading={authorize.isPending || directConnect.isPending}
            >
              {connectMode === 'oauth'
                ? 'Continue to OAuth'
                : isReassign
                  ? 'Assign department'
                  : 'Connect'}
            </Button>
          </div>
        </form>
      </Dialog>

      <ConfirmDialog
        open={Boolean(confirmDisconnect)}
        onOpenChange={(open) => {
          if (!open) setConfirmDisconnect(null);
        }}
        title="Disconnect service"
        description={
          confirmDisconnect
            ? `Disconnecting ${confirmDisconnect.external_account_id} will pause any published department workflows using it. You will need to resume them manually.`
            : ''
        }
        confirmLabel="Disconnect"
        variant="danger"
        loading={revoke.isPending}
        onConfirm={handleConfirmDisconnect}
      />

      <ConfirmDialog
        open={Boolean(confirmDelete)}
        onOpenChange={(open) => {
          if (!open) setConfirmDelete(null);
        }}
        title="Delete connection"
        description={
          confirmDelete
            ? `Permanently delete ${confirmDelete.external_account_id}? Any published department workflows using it will be paused.`
            : ''
        }
        confirmLabel="Delete"
        variant="danger"
        loading={deleteConnection.isPending}
        onConfirm={handleConfirmDelete}
      />
    </div>
  );
}

export default ConnectionsPage;
