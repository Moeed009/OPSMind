import { useState, type FormEvent } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import {
  Activity,
  Archive,
  ArrowLeft,
  CheckCircle2,
  Cpu,
  Edit3,
  PauseCircle,
  Play,
  Plug,
  Plus,
  RefreshCw,
  Trash2,
  UserPlus,
  Users,
  Workflow,
} from 'lucide-react';
import {
  Alert,
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  CardTitle,
  ConfirmDialog,
  Dialog,
  EmptyState,
  ErrorState,
  Input,
  PageHeader,
  RoleBadge,
  Select,
  Skeleton,
  StatCard,
  Tabs,
  TabsContent,
  TabsList,
  TabsTrigger,
  Tooltip,
  useToast,
} from '../components/ui';
import { DeptApprovalsPanel } from '../components/DeptApprovalsPanel';
import { DeptTemplateGallery } from '../components/DeptTemplateGallery';
import { getErrorMessage } from '../lib/api';
import {
  useAuthorize,
  useDeleteConnection,
  useDirectConnect,
  usePoll,
  useRevoke,
} from '../lib/connectionsApi';
import {
  useAssignDepartmentMembers,
  useChangeDepartmentStatus,
  useDepartmentDashboard,
  useRemoveDepartmentMember,
  useUpdateDepartment,
  useUpdateDepartmentMemberRole,
} from '../lib/departmentsApi';
import { useActiveTenant } from '../lib/hooks';
import { useMembers } from '../lib/membersApi';
import type { DepartmentRole } from '../lib/types';
import { useCreateDeptWorkflow, useResumeDeptWorkflow } from '../lib/deptWorkflowsApi';

export default function DepartmentDetailPage() {
  const { id } = useParams<{ id: string }>();
  const departmentId = id ?? '';
  const navigate = useNavigate();
  const { active } = useActiveTenant();
  const { toast } = useToast();

  const isAdminOrOwner = active?.role === 'owner' || active?.role === 'admin';

  const { data: dashboard, isLoading, error, refetch } = useDepartmentDashboard(departmentId);
  const { data: companyMembers } = useMembers();

  const updateDept = useUpdateDepartment(departmentId);
  const statusMutation = useChangeDepartmentStatus();
  const assignMembers = useAssignDepartmentMembers(departmentId);
  const updateMemberRole = useUpdateDepartmentMemberRole(departmentId);
  const removeMember = useRemoveDepartmentMember(departmentId);

  const authorizeConn = useAuthorize();
  const directConnect = useDirectConnect();
  const pollConn = usePoll();
  const revokeConn = useRevoke();
  const deleteConn = useDeleteConnection();
  const createWorkflow = useCreateDeptWorkflow(departmentId);
  const resumeWorkflow = useResumeDeptWorkflow(departmentId);

  const [activeTab, setActiveTab] = useState('overview');
  const [assignModalOpen, setAssignModalOpen] = useState(false);
  const [selectedUserIds, setSelectedUserIds] = useState<string[]>([]);
  const [assignRole, setAssignRole] = useState<DepartmentRole | ''>('');
  const [memberActionError, setMemberActionError] = useState<string | null>(null);

  const [connectModalOpen, setConnectModalOpen] = useState(false);
  const [connProvider, setConnProvider] = useState('gmail');
  const [connAccount, setConnAccount] = useState('');
  const [connToken, setConnToken] = useState('');
  const [disconnectTarget, setDisconnectTarget] = useState<{
    id: string;
    label: string;
  } | null>(null);

  const [wfModalOpen, setWfModalOpen] = useState(false);
  const [wfName, setWfName] = useState('');
  const [wfDesc, setWfDesc] = useState('');

  const [editModalOpen, setEditModalOpen] = useState(false);
  const [editName, setEditName] = useState('');
  const [editDesc, setEditDesc] = useState('');
  const [editCap, setEditCap] = useState('');

  if (isLoading) {
    return (
      <div className="space-y-6" aria-label="Loading department">
        <Skeleton className="h-12 w-72" />
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
          {[1, 2, 3, 4].map((i) => (
            <Skeleton key={i} className="h-28 w-full" />
          ))}
        </div>
        <Skeleton className="h-64 w-full" />
      </div>
    );
  }

  if (error || !dashboard) {
    return (
      <div className="mx-auto max-w-xl py-12 space-y-4">
        <ErrorState
          title="Could not load department"
          message={getErrorMessage(error) || 'Department dashboard unavailable.'}
          onRetry={() => void refetch()}
        />
        <div className="text-center">
          <Button variant="secondary" size="sm" onClick={() => navigate('/departments')}>
            <ArrowLeft className="h-4 w-4" />
            Back to Departments
          </Button>
        </div>
      </div>
    );
  }

  const { department: dept, members, connections, workflows, recent_runs, metrics } = dashboard;
  const isDeptAdmin = isAdminOrOwner || dept.my_effective_role === 'admin';
  const canWriteWorkflows = isDeptAdmin || dept.my_effective_role === 'builder';
  const myRole = dept.my_effective_role ?? active?.role;

  const assignedSet = new Set(members.map((m) => m.user_id));
  const unassignedCompanyMembers = (companyMembers ?? []).filter(
    (m) => !assignedSet.has(m.user_id),
  );
  const pausedWorkflows = workflows.filter((w) => w.latest_version_status === 'paused');

  const openEditModal = () => {
    setEditName(dept.name);
    setEditDesc(dept.description ?? '');
    setEditCap(dept.monthly_ai_cost_cap_usd != null ? String(dept.monthly_ai_cost_cap_usd) : '');
    setEditModalOpen(true);
  };

  const handleSaveEdit = async (e: FormEvent) => {
    e.preventDefault();
    const capVal = editCap.trim() ? Number(editCap) : null;
    await updateDept.mutateAsync({
      name: editName.trim() || dept.name,
      description: editDesc.trim(),
      monthly_ai_cost_cap_usd: capVal,
      clear_cost_cap: !editCap.trim(),
    });
    setEditModalOpen(false);
    toast({ title: 'Department updated', variant: 'success' });
  };

  const handleAssignSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (selectedUserIds.length === 0) return;
    setMemberActionError(null);
    try {
      await assignMembers.mutateAsync({
        user_ids: selectedUserIds,
        role: assignRole || null,
      });
      setSelectedUserIds([]);
      setAssignRole('');
      setAssignModalOpen(false);
      toast({ title: 'Members assigned', variant: 'success' });
    } catch (err) {
      setMemberActionError(getErrorMessage(err));
    }
  };

  const handleDirectConnect = async (e: FormEvent) => {
    e.preventDefault();
    if (!connAccount.trim() || !connToken.trim()) return;
    try {
      await directConnect.mutateAsync({
        provider: connProvider,
        external_account_id: connAccount.trim(),
        credentials: { access_token: connToken.trim() },
        department_id: departmentId,
      });
      setConnAccount('');
      setConnToken('');
      setConnectModalOpen(false);
      toast({ title: 'Connection added', variant: 'success' });
      void refetch();
    } catch {
      // handled by directConnect.error
    }
  };

  const handleOAuthConnect = (provider: string) => {
    authorizeConn.mutate(
      { provider, department_id: departmentId },
      {
        onSuccess: ({ authorize_url }) => {
          window.location.assign(authorize_url);
        },
      },
    );
  };

  const handleCreateDeptWorkflow = async (e: FormEvent) => {
    e.preventDefault();
    if (!wfName.trim()) return;
    const created = await createWorkflow.mutateAsync({
      name: wfName.trim(),
      description: wfDesc.trim() || undefined,
    });
    setWfName('');
    setWfDesc('');
    setWfModalOpen(false);
    navigate(`/departments/${departmentId}/workflows/${created.id}`);
  };

  const handleResumeWorkflow = async (workflowId: string, workflowName: string) => {
    try {
      await resumeWorkflow.mutateAsync(workflowId);
      toast({
        title: `Resumed "${workflowName}"`,
        description: 'Workflow is active again.',
        variant: 'success',
      });
      void refetch();
    } catch (err) {
      toast({
        title: 'Could not resume workflow',
        description: getErrorMessage(err),
        variant: 'danger',
      });
    }
  };

  const handleConfirmDisconnect = async () => {
    if (!disconnectTarget) return;
    try {
      const res = await revokeConn.mutateAsync(disconnectTarget.id);
      const count = res?.paused_workflows?.length ?? 0;
      toast({
        title: 'Connection disconnected',
        description:
          count > 0
            ? `Paused ${count} workflow(s). Resume manually after reconnecting.`
            : undefined,
        variant: count > 0 ? 'warning' : 'success',
      });
      setDisconnectTarget(null);
      void refetch();
    } catch (err) {
      toast({
        title: 'Disconnect failed',
        description: getErrorMessage(err),
        variant: 'danger',
      });
    }
  };

  const statusVariant =
    dept.status === 'active' ? 'success' : dept.status === 'inactive' ? 'warning' : 'neutral';

  return (
    <div className="space-y-6">
      <div className="space-y-4">
        <button
          type="button"
          onClick={() => navigate('/departments')}
          className="inline-flex items-center gap-1.5 text-xs font-medium text-[var(--text-muted)] hover:text-[var(--text)] cursor-pointer"
        >
          <ArrowLeft className="h-3.5 w-3.5" />
          Back to Departments
        </button>

        <PageHeader
          title={dept.name}
          description={dept.description || 'Department operational workspace'}
          badge={
            <div className="flex items-center gap-1.5 flex-wrap">
              <Badge variant="primary" size="xs" className="font-mono">
                {dept.code}
              </Badge>
              <Badge variant={statusVariant} size="xs" className="capitalize">
                {dept.status}
              </Badge>
              {myRole && <RoleBadge role={myRole} />}
            </div>
          }
          actions={
            <div className="flex items-center gap-2 flex-wrap">
              <Button variant="secondary" size="sm" onClick={() => void refetch()}>
                <RefreshCw className="h-3.5 w-3.5" />
                Refresh
              </Button>
              {isAdminOrOwner && (
                <>
                  <Button variant="outline" size="sm" onClick={openEditModal}>
                    <Edit3 className="h-3.5 w-3.5" />
                    Edit
                  </Button>
                  {dept.status === 'active' ? (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() =>
                        statusMutation.mutate({ departmentId, action: 'deactivate' })
                      }
                    >
                      <PauseCircle className="h-3.5 w-3.5" />
                      Deactivate
                    </Button>
                  ) : (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() =>
                        statusMutation.mutate({ departmentId, action: 'activate' })
                      }
                    >
                      <CheckCircle2 className="h-3.5 w-3.5" />
                      Activate
                    </Button>
                  )}
                  {dept.status !== 'archived' && (
                    <Button
                      variant="ghost"
                      size="sm"
                      onClick={() =>
                        statusMutation.mutate({ departmentId, action: 'archive' })
                      }
                    >
                      <Archive className="h-3.5 w-3.5" />
                      Archive
                    </Button>
                  )}
                </>
              )}
            </div>
          }
        />
      </div>

      {pausedWorkflows.length > 0 && (
        <Alert kind="warning">
          <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
            <div>
              <p className="font-semibold text-[var(--text)]">
                {pausedWorkflows.length} workflow{pausedWorkflows.length > 1 ? 's' : ''} paused
              </p>
              <ul className="mt-1 space-y-1">
                {pausedWorkflows.map((wf) => (
                  <li key={wf.id} className="flex items-center gap-2 text-xs text-[var(--text-muted)]">
                    <span className="font-medium text-[var(--text)]">{wf.name}</span>
                    {wf.pause_reason && <span>— {wf.pause_reason}</span>}
                    {(isDeptAdmin || canWriteWorkflows) && (
                      <Button
                        size="sm"
                        variant="secondary"
                        loading={resumeWorkflow.isPending && resumeWorkflow.variables === wf.id}
                        onClick={() => void handleResumeWorkflow(wf.id, wf.name)}
                        className="ml-2 h-6 px-2 text-[11px]"
                      >
                        <Play className="h-3 w-3" />
                        Resume
                      </Button>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          </div>
        </Alert>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          title="Members"
          value={metrics.total_members}
          subtitle={`Assigned to ${dept.code}`}
          icon={Users}
          colorScheme="indigo"
        />
        <StatCard
          title="Connections"
          value={`${metrics.healthy_connections}/${metrics.total_connections}`}
          subtitle="Healthy / Total"
          icon={Plug}
          colorScheme="emerald"
        />
        <StatCard
          title="Workflows"
          value={metrics.total_workflows}
          subtitle={`${metrics.published_workflows} active · ${metrics.paused_workflows} paused`}
          icon={Workflow}
          colorScheme="sky"
        />
        <StatCard
          title="AI Spend (Month)"
          value={`$${Number(metrics.ai_cost_usd).toFixed(4)}`}
          subtitle={`${metrics.ai_total_tokens.toLocaleString()} tokens`}
          icon={Cpu}
          colorScheme="purple"
        />
      </div>

      <Tabs value={activeTab} onValueChange={setActiveTab}>
        <TabsList>
          <TabsTrigger value="overview">Overview</TabsTrigger>
          <TabsTrigger value="members">Members ({members.length})</TabsTrigger>
          <TabsTrigger value="connections">Connections ({connections.length})</TabsTrigger>
          <TabsTrigger value="templates">Templates</TabsTrigger>
          {(isDeptAdmin || myRole === 'reviewer' || isAdminOrOwner) && (
            <TabsTrigger value="approvals">Approvals</TabsTrigger>
          )}
        </TabsList>

        <TabsContent value="overview">
          <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <div className="flex flex-wrap items-center justify-between gap-3 border-b border-[var(--border)] px-5 py-4">
                <div className="flex items-center gap-2">
                  <Workflow className="h-4 w-4 text-[var(--primary)]" />
                  <h3 className="text-sm font-semibold text-[var(--text)]">
                    Department Workflows ({workflows.length})
                  </h3>
                </div>
                {canWriteWorkflows && (
                  <Button size="sm" onClick={() => setWfModalOpen(true)}>
                    <Plus className="h-3.5 w-3.5" />
                    New Workflow
                  </Button>
                )}
              </div>

              <CardContent className="pt-4">
                {workflows.length === 0 ? (
                  <EmptyState
                    icon={Workflow}
                    title="No workflows in this department"
                    description="Create a workflow scoped to this department's connections."
                  />
                ) : (
                  <div className="space-y-2.5">
                    {workflows.map((wf) => (
                      <div
                        key={wf.id}
                        className="flex flex-col gap-3 rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--bg)]/50 p-3.5 transition-colors hover:border-[var(--border-hover)] hover:bg-[var(--surface-muted)]/50 sm:flex-row sm:items-center sm:justify-between"
                      >
                        <div className="min-w-0">
                          <div className="flex items-center gap-2 flex-wrap">
                            <span className="text-sm font-semibold text-[var(--text)]">{wf.name}</span>
                            <Badge
                              variant={
                                wf.latest_version_status === 'published'
                                  ? 'success'
                                  : wf.latest_version_status === 'paused'
                                    ? 'warning'
                                    : 'neutral'
                              }
                              size="xs"
                            >
                              {wf.latest_version_status}
                            </Badge>
                          </div>
                          <p className="mt-1 text-xs text-[var(--text-muted)]">
                            {wf.description || 'Department DAG pipeline'}
                          </p>
                          {wf.latest_version_status === 'paused' && wf.pause_reason && (
                            <p className="mt-1 text-xs text-[var(--warning)]">
                              Paused: {wf.pause_reason}
                            </p>
                          )}
                        </div>

                        <div className="flex items-center gap-2 shrink-0">
                          {wf.latest_version_status === 'paused' && isDeptAdmin && (
                            <Button
                              size="sm"
                              variant="outline"
                              loading={resumeWorkflow.isPending && resumeWorkflow.variables === wf.id}
                              onClick={() => void handleResumeWorkflow(wf.id, wf.name)}
                            >
                              <Play className="h-3.5 w-3.5" />
                              Resume
                            </Button>
                          )}
                          <Button
                            size="sm"
                            variant="secondary"
                            onClick={() => navigate(`/departments/${departmentId}/workflows/${wf.id}`)}
                          >
                            Open Builder
                          </Button>
                        </div>
                      </div>
                    ))}
                  </div>
                )}
              </CardContent>
            </Card>

            <div className="space-y-6">
              <Card className="space-y-3">
                <CardHeader className="pb-0">
                  <CardTitle className="flex items-center gap-2">
                    <Activity className="h-4 w-4 text-[var(--primary)]" />
                    Recent Runs ({recent_runs.length})
                  </CardTitle>
                </CardHeader>
                <CardContent>
                  {recent_runs.length === 0 ? (
                    <p className="py-4 text-xs text-[var(--text-muted)]">
                      No workflow runs executed yet.
                    </p>
                  ) : (
                    <div className="divide-y divide-[var(--border)]">
                      {recent_runs.slice(0, 5).map((r) => (
                        <div key={r.id} className="flex items-center justify-between py-2.5 text-xs">
                          <div className="min-w-0">
                            <p className="truncate font-medium text-[var(--text)]">
                              {r.workflow_name || 'Department Workflow'}
                            </p>
                            <p className="font-mono text-[11px] text-[var(--text-subtle)]">
                              {r.trace_id.slice(0, 10)}
                            </p>
                          </div>
                          <Badge
                            variant={
                              r.status === 'completed'
                                ? 'success'
                                : r.status === 'failed'
                                  ? 'danger'
                                  : 'warning'
                            }
                            size="xs"
                          >
                            {r.status}
                          </Badge>
                        </div>
                      ))}
                    </div>
                  )}
                </CardContent>
              </Card>

              <Card className="space-y-3 text-xs">
                <CardHeader className="pb-0">
                  <CardTitle>AI &amp; Isolation Policy</CardTitle>
                </CardHeader>
                <CardContent className="space-y-2.5">
                  <div className="flex justify-between py-1.5 border-b border-[var(--border)]">
                    <span className="text-[var(--text-muted)]">Department Cap</span>
                    <span className="font-mono text-[var(--text)]">
                      {metrics.department_monthly_cap_usd != null
                        ? `$${Number(metrics.department_monthly_cap_usd).toFixed(2)}`
                        : 'Shared Company Cap'}
                    </span>
                  </div>
                  <div className="flex justify-between py-1.5 border-b border-[var(--border)]">
                    <span className="text-[var(--text-muted)]">Company Cap</span>
                    <span className="font-mono text-[var(--text)]">
                      ${Number(metrics.company_monthly_cap_usd).toFixed(2)}
                    </span>
                  </div>
                  <p className="rounded-[var(--radius-md)] bg-[var(--primary-soft)] p-2.5 text-[11px] text-[var(--text-muted)]">
                    Workflows in <strong className="text-[var(--text)]">{dept.name}</strong> can
                    only reference connections assigned to{' '}
                    <strong className="text-[var(--text)]">{dept.code}</strong>.
                  </p>
                </CardContent>
              </Card>
            </div>
          </div>
        </TabsContent>

        <TabsContent value="members">
          <Card className="space-y-4 p-5">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h3 className="text-sm font-semibold text-[var(--text)]">Department Members</h3>
                <p className="text-xs text-[var(--text-muted)]">
                  Per-department roles override company roles for operations inside {dept.name}.
                </p>
              </div>
              {isAdminOrOwner && (
                <Button
                  size="sm"
                  onClick={() => {
                    setMemberActionError(null);
                    setAssignModalOpen(true);
                  }}
                >
                  <UserPlus className="h-4 w-4" />
                  Assign Members
                </Button>
              )}
            </div>

            {memberActionError && <Alert kind="error">{memberActionError}</Alert>}

            {members.length === 0 ? (
              <EmptyState
                icon={Users}
                title="No members assigned"
                description="Assign company members to grant access to this department."
              />
            ) : (
              <div className="divide-y divide-[var(--border)]">
                {members.map((m) => (
                  <div
                    key={m.user_id}
                    className="flex flex-col gap-3 py-3 sm:flex-row sm:items-center sm:justify-between"
                  >
                    <div>
                      <div className="text-sm font-medium text-[var(--text)]">
                        {m.full_name || m.email}
                      </div>
                      <div className="text-xs text-[var(--text-muted)]">{m.email}</div>
                    </div>

                    <div className="flex items-center gap-2.5 flex-wrap">
                      {m.effective_role === 'reviewer' ? (
                        <Tooltip content="Reviewers approve, edit, or reject AI proposals in the Review Inbox.">
                          <span>
                            <RoleBadge role={m.effective_role} />
                          </span>
                        </Tooltip>
                      ) : (
                        <RoleBadge role={m.effective_role} />
                      )}

                      {isDeptAdmin && (
                        <select
                          aria-label={`Department role for ${m.email}`}
                          value={m.effective_role}
                          disabled={updateMemberRole.isPending}
                          onChange={async (e) => {
                            setMemberActionError(null);
                            try {
                              await updateMemberRole.mutateAsync({
                                userId: m.user_id,
                                role: e.target.value as DepartmentRole,
                              });
                              toast({ title: 'Department role updated', variant: 'success' });
                            } catch (err) {
                              setMemberActionError(getErrorMessage(err));
                            }
                          }}
                          className="h-8 rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface)] px-2.5 text-xs text-[var(--text)] focus:border-[var(--primary)] focus:outline-none"
                        >
                          <option value="admin">Dept Admin</option>
                          <option value="builder">Dept Builder</option>
                          <option value="reviewer">Dept Reviewer</option>
                          <option value="viewer">Dept Viewer</option>
                        </select>
                      )}

                      {isAdminOrOwner && (
                        <Button
                          variant="ghost"
                          size="sm"
                          aria-label={`Remove ${m.email} from department`}
                          onClick={async () => {
                            setMemberActionError(null);
                            try {
                              await removeMember.mutateAsync(m.user_id);
                              toast({ title: 'Member removed from department', variant: 'success' });
                            } catch (err) {
                              setMemberActionError(getErrorMessage(err));
                            }
                          }}
                          className="text-[var(--danger)] hover:bg-[var(--danger-soft)]"
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </Card>
        </TabsContent>

        <TabsContent value="connections">
          <Card className="space-y-4 p-5">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
              <div>
                <h3 className="text-sm font-semibold text-[var(--text)]">
                  Department Connections
                </h3>
                <p className="text-xs text-[var(--text-muted)]">
                  Dedicated mailboxes and integrations scoped exclusively to {dept.name}.
                </p>
              </div>
              {isDeptAdmin && (
                <Button size="sm" onClick={() => setConnectModalOpen(true)}>
                  <Plus className="h-4 w-4" />
                  Connect Department App
                </Button>
              )}
            </div>

            {connections.length === 0 ? (
              <EmptyState
                icon={Plug}
                title="No department connections"
                description={`Connect a Gmail mailbox or Slack channel dedicated to ${dept.name}.`}
              />
            ) : (
              <div className="divide-y divide-[var(--border)]">
                {connections.map((c) => (
                  <div
                    key={c.id}
                    className="flex flex-col gap-3 py-3 sm:flex-row sm:items-center sm:justify-between"
                  >
                    <div>
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-sm font-semibold uppercase text-[var(--text)]">
                          {c.provider}
                        </span>
                        <span className="font-mono text-xs text-[var(--text-muted)]">
                          {c.external_account_id}
                        </span>
                        <Badge
                          variant={c.status === 'CONNECTED' ? 'success' : 'warning'}
                          size="xs"
                        >
                          {c.status}
                        </Badge>
                      </div>
                      {c.last_error && (
                        <p className="mt-1 text-xs text-[var(--danger)]">{c.last_error}</p>
                      )}
                    </div>

                    {isDeptAdmin && (
                      <div className="flex items-center gap-2">
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={() =>
                            pollConn.mutate(c.id, { onSettled: () => void refetch() })
                          }
                        >
                          Poll
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          className="text-[var(--danger)] hover:bg-[var(--danger-soft)]"
                          onClick={() =>
                            setDisconnectTarget({
                              id: c.id,
                              label: c.external_account_id || c.provider,
                            })
                          }
                        >
                          Disconnect
                        </Button>
                        <Button
                          size="sm"
                          variant="ghost"
                          aria-label={`Delete ${c.external_account_id || c.provider}`}
                          onClick={() =>
                            deleteConn.mutate(c.id, { onSuccess: () => void refetch() })
                          }
                        >
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            )}
          </Card>
        </TabsContent>

        <TabsContent value="templates">
          <DeptTemplateGallery
            departmentId={departmentId}
            canUse={canWriteWorkflows}
            onUsed={(wf) => navigate(`/departments/${departmentId}/workflows/${wf.id}`)}
          />
        </TabsContent>

        {(isDeptAdmin || myRole === 'reviewer' || isAdminOrOwner) && (
          <TabsContent value="approvals">
            <DeptApprovalsPanel departmentId={departmentId} />
          </TabsContent>
        )}
      </Tabs>

      <Dialog
        open={assignModalOpen}
        onOpenChange={setAssignModalOpen}
        title={`Assign Members to ${dept.name}`}
      >
        {unassignedCompanyMembers.length === 0 ? (
          <p className="py-4 text-xs text-[var(--text-muted)]">
            All accepted company members are already assigned to {dept.name}.
          </p>
        ) : (
          <form onSubmit={handleAssignSubmit} className="space-y-4">
            <div className="max-h-60 overflow-y-auto divide-y divide-[var(--border)] rounded-[var(--radius-lg)] border border-[var(--border)] p-2">
              {unassignedCompanyMembers.map((m) => {
                const checked = selectedUserIds.includes(m.user_id);
                return (
                  <label
                    key={m.user_id}
                    className="flex items-center justify-between rounded-[var(--radius-md)] px-2 py-2 text-xs hover:bg-[var(--surface-muted)] cursor-pointer"
                  >
                    <div className="flex items-center gap-2.5">
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={(e) => {
                          if (e.target.checked) {
                            setSelectedUserIds((prev) => [...prev, m.user_id]);
                          } else {
                            setSelectedUserIds((prev) => prev.filter((uid) => uid !== m.user_id));
                          }
                        }}
                      />
                      <div>
                        <div className="font-medium text-[var(--text)]">{m.full_name || m.email}</div>
                        <div className="text-[11px] text-[var(--text-muted)]">{m.email}</div>
                      </div>
                    </div>
                    <RoleBadge role={m.role} />
                  </label>
                );
              })}
            </div>

            <Select
              label="Department Role (optional override)"
              value={assignRole}
              onChange={(e) => setAssignRole(e.target.value as DepartmentRole | '')}
            >
              <option value="">Use Company Role (Default)</option>
              <option value="admin">Department Admin</option>
              <option value="builder">Department Builder</option>
              <option value="reviewer">Department Reviewer</option>
              <option value="viewer">Department Viewer</option>
            </Select>

            {assignMembers.isError && (
              <Alert kind="error">{getErrorMessage(assignMembers.error)}</Alert>
            )}

            <div className="flex justify-end gap-2">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => setAssignModalOpen(false)}
              >
                Cancel
              </Button>
              <Button
                type="submit"
                size="sm"
                disabled={selectedUserIds.length === 0}
                loading={assignMembers.isPending}
              >
                Assign Selected ({selectedUserIds.length})
              </Button>
            </div>
          </form>
        )}
      </Dialog>

      <Dialog
        open={connectModalOpen}
        onOpenChange={setConnectModalOpen}
        title={`Connect App for ${dept.name}`}
      >
        <form onSubmit={handleDirectConnect} className="space-y-4">
          {directConnect.isError && (
            <Alert kind="error">{getErrorMessage(directConnect.error)}</Alert>
          )}
          {authorizeConn.isError && (
            <Alert kind="error">{getErrorMessage(authorizeConn.error)}</Alert>
          )}

          <Select
            label="Provider"
            value={connProvider}
            onChange={(e) => setConnProvider(e.target.value)}
          >
            <option value="gmail">Gmail (Department Mailbox)</option>
            <option value="google_drive">Google Drive</option>
            <option value="google_calendar">Google Calendar</option>
            <option value="google_sheets">Google Sheets</option>
            <option value="slack">Slack (Department Channel)</option>
            <option value="hubspot">HubSpot (CRM & Tickets)</option>
            <option value="stripe">Stripe</option>
            <option value="zoho_books">Zoho Books (Finance)</option>
          </Select>

          <Input
            label="Account Identifier"
            required
            placeholder="e.g. support@company.com"
            value={connAccount}
            onChange={(e) => setConnAccount(e.target.value)}
          />

          <Input
            label="Access Token / App Password"
            type="password"
            showPasswordToggle
            required
            placeholder="Paste token or App Password"
            value={connToken}
            onChange={(e) => setConnToken(e.target.value)}
          />

          <div className="flex items-center justify-between pt-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              loading={authorizeConn.isPending}
              onClick={() => handleOAuthConnect(connProvider)}
            >
              Connect via OAuth
            </Button>
            <div className="flex gap-2">
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => setConnectModalOpen(false)}
              >
                Cancel
              </Button>
              <Button type="submit" size="sm" loading={directConnect.isPending}>
                Save Connection
              </Button>
            </div>
          </div>
        </form>
      </Dialog>

      <Dialog open={wfModalOpen} onOpenChange={setWfModalOpen} title={`New Workflow in ${dept.name}`}>
        <form onSubmit={handleCreateDeptWorkflow} className="space-y-4">
          <Input
            label="Workflow Name"
            required
            placeholder="e.g. Inbound Triage"
            value={wfName}
            onChange={(e) => setWfName(e.target.value)}
          />
          <Input
            label="Description (optional)"
            placeholder="What does this workflow automate?"
            value={wfDesc}
            onChange={(e) => setWfDesc(e.target.value)}
          />
          <div className="flex items-center justify-between gap-2">
            <Button
              type="button"
              variant="ghost"
              size="sm"
              onClick={() => {
                setWfModalOpen(false);
                setActiveTab('templates');
              }}
            >
              Browse Templates
            </Button>
            <div className="flex justify-end gap-2">
              <Button type="button" variant="ghost" size="sm" onClick={() => setWfModalOpen(false)}>
                Cancel
              </Button>
              <Button type="submit" size="sm" loading={createWorkflow.isPending}>
                Create &amp; Open Builder
              </Button>
            </div>
          </div>
        </form>
      </Dialog>

      <Dialog open={editModalOpen} onOpenChange={setEditModalOpen} title="Edit Department">
        <form onSubmit={handleSaveEdit} className="space-y-4">
          <Input
            label="Name"
            required
            value={editName}
            onChange={(e) => setEditName(e.target.value)}
          />
          <Input
            label="Description"
            value={editDesc}
            onChange={(e) => setEditDesc(e.target.value)}
          />
          <Input
            label="Monthly AI Cost Cap (USD)"
            type="number"
            min="0"
            step="0.01"
            placeholder="Leave blank for shared company cap"
            value={editCap}
            onChange={(e) => setEditCap(e.target.value)}
          />
          <div className="flex justify-end gap-2">
            <Button type="button" variant="ghost" size="sm" onClick={() => setEditModalOpen(false)}>
              Cancel
            </Button>
            <Button type="submit" size="sm" loading={updateDept.isPending}>
              Save Changes
            </Button>
          </div>
        </form>
      </Dialog>

      <ConfirmDialog
        open={Boolean(disconnectTarget)}
        onOpenChange={(open) => {
          if (!open) setDisconnectTarget(null);
        }}
        title="Disconnect connection?"
        description={`Disconnecting ${disconnectTarget?.label ?? 'this service'} will erase stored credentials and automatically pause any published workflows in ${dept.name} that use it.`}
        confirmLabel="Disconnect & Pause Workflows"
        loading={revokeConn.isPending}
        onConfirm={handleConfirmDisconnect}
      />
    </div>
  );
}
