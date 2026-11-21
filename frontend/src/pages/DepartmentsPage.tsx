import { useMemo, useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { ArrowRight, Building2, PauseCircle, Plug, Plus, Search, Sparkles, Users } from 'lucide-react';
import {
  Alert,
  Badge,
  Button,
  Dialog,
  EmptyState,
  ErrorState,
  Input,
  PageHeader,
  RoleBadge,
  Skeleton,
} from '../components/ui';
import { getErrorMessage } from '../lib/api';
import {
  useCreateDepartment,
  useDepartments,
  useDepartmentTemplates,
  type DepartmentTemplate,
} from '../lib/departmentsApi';
import { useActiveTenant } from '../lib/hooks';
import { useAllDeptWorkflows } from '../lib/deptWorkflowsApi';

const GRADIENT_STRIPS = [
  'from-indigo-500 to-purple-500',
  'from-sky-500 to-cyan-500',
  'from-emerald-500 to-teal-500',
  'from-orange-500 to-amber-500',
  'from-rose-500 to-pink-500',
];

export default function DepartmentsPage() {
  const navigate = useNavigate();
  const { active } = useActiveTenant();
  const canManage = active?.role === 'owner' || active?.role === 'admin';

  const [showArchived, setShowArchived] = useState(false);
  const [searchQuery, setSearchQuery] = useState('');
  const [createOpen, setCreateOpen] = useState(false);

  const [name, setName] = useState('');
  const [code, setCode] = useState('');
  const [description, setDescription] = useState('');
  const [selectedTemplate, setSelectedTemplate] = useState('');
  const [costCap, setCostCap] = useState('');

  const { data: departments, isLoading, error, refetch } = useDepartments(showArchived);
  const { data: templates } = useDepartmentTemplates();
  const { data: workflows } = useAllDeptWorkflows();
  const createMutation = useCreateDepartment();

  const pausedByDept = useMemo(() => {
    const map = new Map<string, number>();
    for (const wf of workflows ?? []) {
      if (wf.department_id && wf.status === 'paused') {
        map.set(wf.department_id, (map.get(wf.department_id) ?? 0) + 1);
      }
    }
    return map;
  }, [workflows]);

  const filtered = useMemo(() => {
    if (!departments) return [];
    if (!searchQuery.trim()) return departments;
    const q = searchQuery.toLowerCase();
    return departments.filter(
      (d) =>
        d.name.toLowerCase().includes(q) ||
        d.code.toLowerCase().includes(q) ||
        (d.description && d.description.toLowerCase().includes(q)),
    );
  }, [departments, searchQuery]);

  const applyTemplate = (tpl: DepartmentTemplate) => {
    setSelectedTemplate(tpl.key);
    setName(tpl.name);
    setCode(tpl.code);
    setDescription(tpl.description);
  };

  const resetModal = () => {
    setName('');
    setCode('');
    setDescription('');
    setSelectedTemplate('');
    setCostCap('');
    setCreateOpen(false);
  };

  const handleCreate = async (e: FormEvent) => {
    e.preventDefault();
    if (!name.trim()) return;
    const parsedCap = costCap.trim() ? Number(costCap) : null;
    try {
      const created = await createMutation.mutateAsync({
        name: name.trim(),
        code: code.trim() || undefined,
        description: description.trim() || undefined,
        template_key: selectedTemplate || undefined,
        monthly_ai_cost_cap_usd: parsedCap !== null && !Number.isNaN(parsedCap) ? parsedCap : null,
      });
      resetModal();
      navigate(`/departments/${created.id}`);
    } catch {
      // handled by createMutation.error
    }
  };

  return (
    <div className="space-y-6">
      <PageHeader
        title="Departments"
        description="Isolate members, mailboxes, workflows, and AI budgets by department."
        badge={
          <Badge variant="primary" size="sm">
            <Building2 className="h-3 w-3" />
            Operational Scopes
          </Badge>
        }
        actions={
          canManage ? (
            <button
              onClick={() => setCreateOpen(true)}
              className="inline-flex items-center gap-2 rounded-[var(--radius-lg)] bg-gradient-to-r from-[var(--primary)] to-purple-500 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:opacity-90 transition-opacity"
            >
              <Plus className="h-4 w-4" />
              New Department
            </button>
          ) : undefined
        }
      />

      <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
        <div className="relative w-full sm:max-w-sm">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-[var(--text-subtle)]" />
          <Input
            aria-label="Search departments"
            placeholder="Search departments..."
            value={searchQuery}
            onChange={(e) => setSearchQuery(e.target.value)}
            className="pl-9 h-10 rounded-[var(--radius-xl)]"
          />
        </div>

        <label className="inline-flex items-center gap-2 text-xs text-[var(--text-muted)] cursor-pointer select-none">
          <input
            type="checkbox"
            checked={showArchived}
            onChange={(e) => setShowArchived(e.target.checked)}
            className="rounded border-[var(--border)] bg-[var(--surface)] text-[var(--primary)]"
          />
          Show archived departments
        </label>
      </div>

      {isLoading ? (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5" aria-label="Loading departments">
          {[1, 2, 3].map((i) => (
            <div key={i} className="rounded-[var(--radius-xl)] border border-[var(--border)] bg-[var(--surface)] shadow-[var(--shadow-sm)] overflow-hidden space-y-4 p-5">
              <Skeleton className="h-5 w-2/3" />
              <Skeleton className="h-4 w-full" />
              <div className="flex gap-2">
                <Skeleton className="h-6 w-20" />
                <Skeleton className="h-6 w-24" />
              </div>
            </div>
          ))}
        </div>
      ) : error ? (
        <ErrorState
          title="Could not load departments"
          message={getErrorMessage(error)}
          onRetry={() => void refetch()}
        />
      ) : filtered.length === 0 ? (
        <EmptyState
          icon={Building2}
          title={searchQuery ? 'No matching departments' : 'No departments yet'}
          description={
            searchQuery
              ? 'Try clearing your search query or showing archived departments.'
              : canManage
                ? 'Create your first department to isolate mailboxes, members, and AI workflows.'
                : 'You are not assigned to any department yet. Ask your company admin to assign you.'
          }
          action={
            canManage && !searchQuery ? (
              <button
                onClick={() => setCreateOpen(true)}
                className="inline-flex items-center gap-2 rounded-[var(--radius-lg)] bg-gradient-to-r from-[var(--primary)] to-purple-500 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:opacity-90 transition-opacity"
              >
                <Plus className="h-4 w-4" />
                New Department
              </button>
            ) : undefined
          }
        />
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
          {filtered.map((dept, idx) => {
            const pausedCount = pausedByDept.get(dept.id) ?? 0;
            const myRole = dept.my_effective_role ?? active?.role;
            const statusVariant =
              dept.status === 'active'
                ? 'success'
                : dept.status === 'inactive'
                  ? 'warning'
                  : 'neutral';
            const gradientClass = GRADIENT_STRIPS[idx % GRADIENT_STRIPS.length];

            return (
              <div
                key={dept.id}
                className="group rounded-[var(--radius-xl)] border border-[var(--border)] bg-[var(--surface)] shadow-[var(--shadow-sm)] hover:shadow-[var(--shadow-md)] hover:-translate-y-1 transition-all duration-200 overflow-hidden cursor-pointer flex flex-col justify-between"
                onClick={() => navigate(`/departments/${dept.id}`)}
              >
                {/* Colored top strip */}
                <div className={`h-[3px] w-full bg-gradient-to-r ${gradientClass}`} />

                <div className="p-5 space-y-3 flex-1">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <h3 className="truncate text-base font-bold text-[var(--text)]">{dept.name}</h3>
                      <span className="mt-1 inline-block font-mono text-[11px] text-[var(--text-subtle)]">
                        {dept.code}
                      </span>
                    </div>
                    <Badge variant={statusVariant} size="xs" className="capitalize shrink-0">
                      {dept.status}
                    </Badge>
                  </div>

                  {dept.description && (
                    <p className="line-clamp-2 text-xs text-[var(--text-muted)]">
                      {dept.description}
                    </p>
                  )}

                  <div className="flex flex-wrap items-center gap-1.5">
                    <Badge variant="neutral" size="xs">
                      <Users className="h-3 w-3" />
                      {dept.member_count} {dept.member_count === 1 ? 'member' : 'members'}
                    </Badge>

                    <Badge
                      variant={dept.connection_count > 0 ? 'success' : 'neutral'}
                      size="xs"
                    >
                      <Plug className="h-3 w-3" />
                      {dept.connection_count > 0
                        ? `${dept.connection_count} mailbox${dept.connection_count > 1 ? 'es' : ''}`
                        : 'No mailbox'}
                    </Badge>

                    {pausedCount > 0 && (
                      <Badge variant="warning" size="xs">
                        <PauseCircle className="h-3 w-3" />
                        {pausedCount} paused
                      </Badge>
                    )}
                  </div>
                </div>

                <div className="flex items-center justify-between border-t border-[var(--border)] px-5 py-3 text-xs">
                  <div className="flex items-center gap-1.5">
                    <span className="text-[var(--text-subtle)]">My role:</span>
                    {myRole && <RoleBadge role={myRole} />}
                  </div>
                  <span className="inline-flex items-center gap-1 font-semibold text-[var(--primary)] opacity-0 group-hover:opacity-100 transition-opacity">
                    Open
                    <ArrowRight className="h-3.5 w-3.5" />
                  </span>
                </div>
              </div>
            );
          })}
        </div>
      )}

      <Dialog
        open={createOpen}
        onOpenChange={(open) => {
          if (!open) resetModal();
          else setCreateOpen(true);
        }}
        title="Create Department"
        description="Start from a recommended template or create a custom operational scope."
      >
        <form onSubmit={handleCreate} className="space-y-4">
          {templates && templates.length > 0 && (
            <div className="space-y-1.5">
              <span className="block text-xs font-medium text-[var(--text-muted)]">
                Starter Templates
              </span>
              <div className="flex flex-wrap gap-1.5">
                {templates.map((tpl) => {
                  const selected = selectedTemplate === tpl.key;
                  return (
                    <button
                      key={tpl.key}
                      type="button"
                      onClick={() => applyTemplate(tpl)}
                      className={`inline-flex items-center gap-1 rounded-[var(--radius-md)] border px-2.5 py-1 text-xs font-medium transition-colors cursor-pointer ${
                        selected
                          ? 'border-[var(--primary)] bg-[var(--primary-soft)] text-[var(--primary)]'
                          : 'border-[var(--border)] bg-[var(--surface-muted)] text-[var(--text-muted)] hover:text-[var(--text)]'
                      }`}
                    >
                      <Sparkles className="h-3 w-3" />
                      {tpl.name}
                    </button>
                  );
                })}
              </div>
            </div>
          )}

          {createMutation.isError && (
            <Alert kind="error">{getErrorMessage(createMutation.error)}</Alert>
          )}

          <Input
            label="Department Name"
            required
            placeholder="e.g. Customer Support"
            value={name}
            onChange={(e) => setName(e.target.value)}
          />
          <Input
            label="Department Code (optional)"
            placeholder="e.g. support"
            hint="Lowercase letters, numbers, or hyphens. Unique per company."
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
          <Input
            label="Description (optional)"
            placeholder="What workflows and mailboxes belong here?"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
          />
          <Input
            label="Monthly AI Cost Cap USD (optional)"
            type="number"
            min="0"
            step="0.01"
            placeholder="Leave blank for shared company cap"
            value={costCap}
            onChange={(e) => setCostCap(e.target.value)}
          />

          <div className="flex justify-end gap-2 pt-2">
            <Button type="button" variant="ghost" size="sm" onClick={resetModal}>
              Cancel
            </Button>
            <Button type="submit" size="sm" loading={createMutation.isPending}>
              Create department
            </Button>
          </div>
        </form>
      </Dialog>
    </div>
  );
}
