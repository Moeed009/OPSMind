import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import {
  Activity,
  ArrowUpRight,
  Award,
  Building2,
  CheckCircle2,
  Clock,
  Database,
  Globe,
  Lock,
  Plug,
  Plus,
  ShieldCheck,
  ShieldAlert,
  Workflow,
} from 'lucide-react';
import {
  Alert,
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  EmptyState,
  RoleBadge,
  Skeleton,
  StatCard,
} from '../components/ui';
import { getErrorMessage } from '../lib/api';
import { useActiveTenant, useCurrentTenant, useMe } from '../lib/hooks';
import { useAllDeptWorkflows } from '../lib/deptWorkflowsApi';
import { useConnections } from '../lib/connectionsApi';
import { fetchRuns } from '../lib/runsApi';
import { fetchReviews } from '../lib/reviewsApi';
import { REGION_LABELS } from '../lib/types';

function runStatusVariant(status: string): 'success' | 'warning' | 'danger' | 'primary' | 'neutral' {
  switch (status) {
    case 'succeeded':
      return 'success';
    case 'waiting_human':
    case 'paused':
      return 'warning';
    case 'failed':
      return 'danger';
    case 'running':
      return 'primary';
    default:
      return 'neutral';
  }
}

function runStatusAccent(status: string): string {
  switch (status) {
    case 'succeeded':
      return 'border-l-2 border-[var(--success)] bg-gradient-to-r from-[color-mix(in_srgb,var(--success)_8%,transparent)] to-transparent';
    case 'failed':
      return 'border-l-2 border-[var(--danger)] bg-gradient-to-r from-[color-mix(in_srgb,var(--danger)_8%,transparent)] to-transparent';
    case 'running':
      return 'border-l-2 border-[var(--primary)] bg-gradient-to-r from-[color-mix(in_srgb,var(--primary)_8%,transparent)] to-transparent';
    case 'waiting_human':
    case 'paused':
      return 'border-l-2 border-[var(--warning)] bg-gradient-to-r from-[color-mix(in_srgb,var(--warning)_8%,transparent)] to-transparent';
    default:
      return 'border-l-2 border-[var(--border)] bg-transparent';
  }
}

export default function DashboardPage() {
  const { data: me } = useMe();
  const { active } = useActiveTenant();
  const tenant = useCurrentTenant(active?.id);
  const { data: workflows, isLoading: wfLoading } = useAllDeptWorkflows();
  const { data: connections, isLoading: connLoading } = useConnections();

  const { data: recentRuns, isLoading: runsLoading } = useQuery({
    queryKey: ['runs', 'recent', active?.id],
    queryFn: () => fetchRuns({ excludeLegacy: true }),
    enabled: Boolean(active?.id),
    refetchInterval: 10000,
  });

  const { data: pendingReviews } = useQuery({
    queryKey: ['reviews', 'open', active?.id],
    queryFn: () => fetchReviews('open'),
    enabled: Boolean(active?.id),
    refetchInterval: 15000,
  });

  const totalWorkflows = workflows?.length ?? 0;
  const publishedWorkflows =
    workflows?.filter((w) => w.status === 'active' || w.active_version != null).length ?? 0;
  const draftWorkflows = totalWorkflows - publishedWorkflows;

  const connectedTools = connections?.filter((c) => c.status === 'CONNECTED').length ?? 0;
  const totalTools = connections?.length ?? 0;

  const activeReviews =
    pendingReviews?.filter((t) => t.department_id != null && !t.is_legacy_readonly) ?? [];
  const pendingCount = activeReviews.length;

  const activeRuns = recentRuns?.filter((r) => r.department_id !== null) ?? [];
  const totalRuns = activeRuns.length;
  const succeededRuns =
    activeRuns.filter((r) => r.status === 'succeeded' || (r.status as string) === 'completed')
      .length ?? 0;
  const successRate = totalRuns > 0 ? Math.round((succeededRuns / totalRuns) * 100) : 100;

  const canCreateWorkflow =
    active?.role === 'owner' || active?.role === 'admin' || active?.role === 'builder';
  const canManageConnectors = active?.role === 'owner' || active?.role === 'admin';

  const userName = me?.full_name || me?.email?.split('@')[0] || 'Operator';

  return (
    <div className="space-y-6">
      {/* ── Welcome Banner ── */}
      <div className="relative overflow-hidden rounded-[var(--radius-2xl)] border border-[var(--border)] bg-gradient-to-r from-[var(--primary)]/5 via-[var(--surface)] to-purple-500/5 p-6">
        {/* Decorative orbs */}
        <div className="absolute -right-16 -top-16 h-48 w-48 rounded-full bg-[var(--primary)]/8 blur-3xl pointer-events-none" />
        <div className="absolute -left-10 -bottom-10 h-32 w-32 rounded-full bg-purple-500/6 blur-2xl pointer-events-none" />
        <div className="relative">
          <div className="flex flex-col sm:flex-row sm:items-center sm:justify-between gap-4">
            <div>
              <h1 className="text-2xl font-bold tracking-tight text-[var(--text)]">
                Welcome back, {userName} 👋
              </h1>
              <p className="mt-1 text-sm text-[var(--text-muted)] max-w-lg">
                Orchestrate department workflows, monitor live executions, and review AI-gated actions.
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {canCreateWorkflow && (
                <Link to="/departments">
                  <Button variant="primary" size="sm">
                    <Plus className="h-4 w-4" />
                    New Workflow
                  </Button>
                </Link>
              )}
              {canManageConnectors && (
                <Link to="/connectors">
                  <Button variant="secondary" size="sm">
                    <Plug className="h-4 w-4" />
                    Manage Connectors
                  </Button>
                </Link>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* ── Stat Cards ── */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-4">
        <StatCard
          title="Active Workflows"
          value={wfLoading ? '—' : totalWorkflows}
          subtitle={`${publishedWorkflows} published • ${draftWorkflows} draft`}
          icon={Workflow}
          colorScheme="indigo"
        />
        <StatCard
          title="Live Success Rate"
          value={runsLoading ? '—' : `${successRate}%`}
          subtitle={`${succeededRuns} of ${totalRuns} runs passed`}
          icon={Activity}
          trend={totalRuns > 0 ? `${totalRuns} recent` : 'Standing by'}
          colorScheme="emerald"
        />
        <StatCard
          title="Connected Services"
          value={connLoading ? '—' : connectedTools}
          subtitle={`${totalTools} total configured`}
          icon={Plug}
          colorScheme="sky"
        />
        <StatCard
          title="Human Approval Queue"
          value={pendingCount > 0 ? `${pendingCount} Pending` : 'Zero Backlog'}
          subtitle="AI guardrail gating active"
          icon={ShieldCheck}
          trend={pendingCount > 0 ? 'Action Needed' : 'Nominal'}
          colorScheme="amber"
        />
      </div>

      {/* ── Main content grid ── */}
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-3">

        {/* ── Workflows & Pipelines ── */}
        <Card className="lg:col-span-2 flex flex-col justify-between overflow-hidden">
          {/* gradient top bar */}
          <div className="h-0.5 bg-gradient-to-r from-[var(--primary)] to-purple-500" />
          <div>
            <CardHeader className="border-b border-[var(--border)]">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Workflow className="h-4 w-4 text-[var(--primary)]" />
                  <h2 className="text-sm font-semibold text-[var(--text)]">
                    Workflows &amp; Pipelines
                  </h2>
                </div>
                <Link
                  to="/departments"
                  className="inline-flex items-center gap-1 text-xs font-medium text-[var(--primary)] hover:underline"
                >
                  View all <ArrowUpRight className="h-3.5 w-3.5" />
                </Link>
              </div>
            </CardHeader>

            <CardContent className="pt-4">
              {wfLoading ? (
                <div className="space-y-2.5">
                  <Skeleton className="h-12 w-full" />
                  <Skeleton className="h-12 w-full" />
                  <Skeleton className="h-12 w-full" />
                </div>
              ) : workflows && workflows.length > 0 ? (
                <div className="space-y-1">
                  {workflows.slice(0, 4).map((wf) => {
                    const isPublished = wf.status === 'active' || wf.active_version != null;
                    return (
                      <Link
                        key={wf.id}
                        to={`/departments/${wf.department_id}/workflows/${wf.id}`}
                        className="group flex items-center justify-between gap-4 hover:bg-[var(--surface-muted)] rounded-[var(--radius-lg)] px-3 py-3 transition-all duration-150"
                      >
                        <div className="min-w-0 flex items-center gap-2.5">
                          {/* colored dot indicator */}
                          <span
                            className={`h-2 w-2 shrink-0 rounded-full ${
                              isPublished ? 'bg-[var(--success)]' : 'bg-[var(--warning)]'
                            }`}
                          />
                          <div className="min-w-0">
                            <div className="flex items-center gap-2">
                              <span className="truncate text-sm font-semibold text-[var(--text)] group-hover:text-[var(--primary)]">
                                {wf.name}
                              </span>
                              <span className="font-mono text-[11px] text-[var(--text-subtle)]">
                                v{wf.version}
                              </span>
                            </div>
                            <p className="mt-0.5 max-w-md truncate text-xs text-[var(--text-muted)]">
                              {wf.description || 'No description provided.'}
                            </p>
                          </div>
                        </div>

                        <div className="flex shrink-0 items-center gap-2.5">
                          <Badge variant={isPublished ? 'success' : 'warning'}>
                            {isPublished ? (
                              <>
                                <CheckCircle2 className="h-3 w-3" /> Published
                              </>
                            ) : (
                              <>
                                <Clock className="h-3 w-3" /> Draft
                              </>
                            )}
                          </Badge>
                          <ArrowUpRight className="h-3.5 w-3.5 text-[var(--text-subtle)] group-hover:text-[var(--primary)]" />
                        </div>
                      </Link>
                    );
                  })}
                </div>
              ) : (
                <EmptyState
                  icon={Workflow}
                  title="No automated workflows yet"
                  description="Create your first workflow to automate department operations."
                  action={
                    canCreateWorkflow ? (
                      <Link to="/departments">
                        <Button size="sm">Create workflow</Button>
                      </Link>
                    ) : undefined
                  }
                />
              )}
            </CardContent>
          </div>

          {/* ── Security/Compliance footer bar ── */}
          <div className="mx-4 mb-4 mt-4 flex flex-wrap items-center justify-between gap-3 rounded-[var(--radius-lg)] border border-[var(--success)]/20 bg-[var(--success-soft)] px-4 py-2.5 text-xs text-[var(--text-muted)]">
            <div className="flex items-center gap-2">
              <ShieldAlert className="h-3.5 w-3.5 text-[var(--success)]" />
              <span>
                PII Redaction Active:{' '}
                <strong className="text-[var(--success)]">Enforcing</strong>
              </span>
            </div>
            <div className="flex items-center gap-2">
              <Lock className="h-3.5 w-3.5 text-[var(--success)]" />
              <span>
                Policy Guardrails Enforced:{' '}
                <strong className="text-[var(--text)]">$500 Limit Auto-Escalation</strong>
              </span>
            </div>
          </div>
        </Card>

        {/* ── Right column ── */}
        <div className="space-y-6">

          {/* ── Live DAG Telemetry ── */}
          <Card className="overflow-hidden">
            {/* gradient top bar */}
            <div className="h-0.5 bg-gradient-to-r from-[var(--success)] to-[var(--primary)]" />
            <CardHeader className="border-b border-[var(--border)]">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Activity className="h-4 w-4 text-[var(--success)]" />
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-[var(--text)]">
                    Live DAG Telemetry
                  </h3>
                </div>
                <span className="font-mono text-[10px] text-[var(--text-subtle)]">Real-time</span>
              </div>
            </CardHeader>

            <CardContent className="pt-4">
              {runsLoading ? (
                <div className="space-y-2">
                  <Skeleton className="h-11 w-full" />
                  <Skeleton className="h-11 w-full" />
                </div>
              ) : activeRuns.length > 0 ? (
                <div className="space-y-2">
                  {activeRuns.slice(0, 4).map((run) => (
                    <div
                      key={run.id}
                      className={`flex items-center justify-between gap-2 rounded-[var(--radius-md)] border border-[var(--border)] p-2.5 pl-3 text-xs transition-all duration-150 ${runStatusAccent(run.status)}`}
                    >
                      <div className="min-w-0">
                        <p className="truncate font-medium text-[var(--text)]">
                          {run.workflow_name || `Run #${run.id.slice(0, 8)}`}
                        </p>
                        <p className="mt-0.5 font-mono text-[10px] text-[var(--text-subtle)]">
                          Trace: {run.trace_id ? run.trace_id.slice(0, 10) : 'manual'}
                        </p>
                      </div>
                      <Badge variant={runStatusVariant(run.status)}>
                        {run.status.replace('_', ' ')}
                      </Badge>
                    </div>
                  ))}
                </div>
              ) : (
                <p className="py-4 text-center text-xs text-[var(--text-subtle)]">
                  No recent DAG executions recorded yet.
                </p>
              )}
            </CardContent>
          </Card>

          {/* ── Organization Specs ── */}
          <Card className="overflow-hidden">
            {/* gradient top bar */}
            <div className="h-0.5 bg-gradient-to-r from-[var(--primary)] to-purple-500" />
            <CardHeader className="border-b border-[var(--border)]">
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2">
                  <Building2 className="h-4 w-4 text-[var(--primary)]" />
                  <h3 className="text-xs font-semibold uppercase tracking-wider text-[var(--text)]">
                    Organization Specs
                  </h3>
                </div>
                {tenant.data && <RoleBadge role={tenant.data.role} />}
              </div>
            </CardHeader>

            <CardContent className="pt-4">
              {tenant.isPending && (
                <div className="space-y-2">
                  <Skeleton className="h-6 w-full" />
                  <Skeleton className="h-6 w-full" />
                </div>
              )}

              {tenant.isError && <Alert kind="error">{getErrorMessage(tenant.error)}</Alert>}

              {tenant.data && (
                <div className="divide-y divide-[var(--border)] text-xs">
                  {/* Tenant Name */}
                  <div className="flex items-center justify-between py-2.5">
                    <span className="flex items-center gap-1.5 text-[var(--text-muted)]">
                      <Building2 className="h-3.5 w-3.5 text-[var(--text-subtle)]" />
                      Tenant Name
                    </span>
                    <span className="font-semibold text-[var(--text)]">{tenant.data.name}</span>
                  </div>
                  {/* Data Isolation */}
                  <div className="flex items-center justify-between py-2.5">
                    <span className="flex items-center gap-1.5 text-[var(--text-muted)]">
                      <Database className="h-3.5 w-3.5 text-[var(--text-subtle)]" />
                      Data Isolation
                    </span>
                    <span className="font-mono font-medium text-[var(--success)]">RLS Active</span>
                  </div>
                  {/* Region */}
                  <div className="flex items-center justify-between py-2.5">
                    <span className="flex items-center gap-1.5 text-[var(--text-muted)]">
                      <Globe className="h-3.5 w-3.5 text-[var(--text-subtle)]" />
                      Region
                    </span>
                    <span className="font-semibold text-[var(--text)]">
                      {REGION_LABELS[tenant.data.region] || tenant.data.region}
                    </span>
                  </div>
                  {/* Plan Tier — gradient badge */}
                  <div className="flex items-center justify-between py-2.5">
                    <span className="flex items-center gap-1.5 text-[var(--text-muted)]">
                      <Award className="h-3.5 w-3.5 text-[var(--text-subtle)]" />
                      Plan Tier
                    </span>
                    <span className="bg-gradient-to-r from-[var(--primary)] to-purple-500 text-white text-xs font-bold px-2.5 py-0.5 rounded-full">
                      {tenant.data.plan.toUpperCase()}
                    </span>
                  </div>
                </div>
              )}
            </CardContent>
          </Card>

        </div>
      </div>
    </div>
  );
}
