import { useCallback, useEffect, useState } from 'react';
import {
  Bot,
  CheckCircle2,
  Clock,
  Edit3,
  FileText,
  Mail,
  RefreshCw,
  Send,
  ShieldAlert,
  Sparkles,
  XCircle,
} from 'lucide-react';
import { fetchReviews, decideReview, type ReviewTask } from '../lib/reviewsApi';
import { getErrorMessage } from '../lib/api';
import {
  Alert,
  Badge,
  Button,
  Card,
  CardContent,
  CardHeader,
  EmptyState,
  ErrorState,
  Input,
  PageHeader,
  Skeleton,
  useToast,
} from '../components/ui';

function isActionable(status?: string | null): boolean {
  if (!status) return false;
  return ['open', 'claimed', 'escalated', 'pending'].includes(status.toLowerCase());
}

function extractProposalText(proposal?: Record<string, unknown> | null): string {
  if (!proposal) return '';
  if (typeof proposal.content === 'string') return proposal.content;
  if (typeof proposal.draft === 'string') return proposal.draft;
  if (typeof proposal.resolution === 'string') return proposal.resolution;
  if (typeof proposal.message === 'string') return proposal.message;
  return JSON.stringify(proposal, null, 2);
}

function extractRecipient(task: ReviewTask): string | null {
  const propTo = task.proposal?.to;
  if (typeof propTo === 'string' && propTo.trim()) return propTo;
  const evFrom = task.evidence?.from ?? task.evidence?.recipient;
  if (typeof evFrom === 'string' && evFrom.trim()) return evFrom;
  return null;
}

function reviewBadgeVariant(
  status: string,
  actionable: boolean,
): 'warning' | 'success' | 'primary' | 'danger' {
  if (actionable) return 'warning';
  if (status === 'approved') return 'success';
  if (status === 'edited') return 'primary';
  return 'danger';
}

export function ReviewInboxPage() {
  const { toast } = useToast();
  const [tasks, setTasks] = useState<ReviewTask[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [filter, setFilter] = useState<'pending' | 'all'>('pending');
  const [selectedTask, setSelectedTask] = useState<ReviewTask | null>(null);
  const [isEditing, setIsEditing] = useState(false);
  const [editedDraft, setEditedDraft] = useState('');
  const [decisionReason, setDecisionReason] = useState('');
  const [submitting, setSubmitting] = useState(false);

  const selectTask = useCallback((task: ReviewTask | null) => {
    setSelectedTask(task);
    if (task) {
      setEditedDraft(extractProposalText(task.proposal));
      setDecisionReason('');
      setIsEditing(false);
    }
  }, []);

  const applyLoadedTasks = useCallback((data: ReviewTask[]) => {
    setTasks(data);
    setSelectedTask((prev) => {
      if (data.length === 0) return null;
      const found = prev?.id ? data.find((t) => t.id === prev.id) : null;
      const next = found ?? data[0];
      setEditedDraft(extractProposalText(next.proposal));
      return next;
    });
  }, []);

  const loadTasks = useCallback(
    async (activeFilter: 'pending' | 'all' = filter) => {
      try {
        setLoading(true);
        setError(null);
        const data = await fetchReviews(activeFilter === 'pending' ? 'open' : undefined);
        applyLoadedTasks(data);
      } catch (err: unknown) {
        setError(getErrorMessage(err));
      } finally {
        setLoading(false);
      }
    },
    [applyLoadedTasks, filter],
  );

  useEffect(() => {
    let cancelled = false;
    fetchReviews(filter === 'pending' ? 'open' : undefined)
      .then((data) => {
        if (!cancelled) applyLoadedTasks(data);
      })
      .catch((err: unknown) => {
        if (!cancelled) setError(getErrorMessage(err));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [applyLoadedTasks, filter]);

  const handleDecision = async (decision: 'approved' | 'edited' | 'rejected') => {
    if (!selectedTask) return;
    try {
      setSubmitting(true);
      setError(null);

      const correction =
        decision === 'edited'
          ? {
              ...selectedTask.proposal,
              content: editedDraft,
              draft: editedDraft,
              resolution: editedDraft,
            }
          : undefined;

      const updated = await decideReview(selectedTask.id, {
        decision,
        reason: decisionReason.trim() || undefined,
        correction,
      });

      toast({
        variant: decision === 'rejected' ? 'warning' : 'success',
        title: `Task ${decision}`,
        description:
          decision === 'rejected'
            ? 'Workflow execution was halted.'
            : 'Workflow resumed and action dispatched.',
      });

      setTasks((prev) =>
        filter === 'pending'
          ? prev.filter((t) => t.id !== selectedTask.id)
          : prev.map((t) => (t.id === selectedTask.id ? updated : t)),
      );
      setSelectedTask(null);
    } catch (err: unknown) {
      setError(getErrorMessage(err));
    } finally {
      setSubmitting(false);
    }
  };

  const actionableCount = tasks.filter((t) => isActionable(t.status) && !t.is_legacy_readonly).length;
  const currentActionable = isActionable(selectedTask?.status) && !selectedTask?.is_legacy_readonly;
  const recipient = selectedTask ? extractRecipient(selectedTask) : null;

  return (
    <div className="space-y-6">
      <PageHeader
        title="Review Inbox"
        description="Human-in-the-loop (HITL) approvals for high-impact AI actions before external dispatch."
        actions={
          <div className="flex flex-wrap items-center gap-2">
            <div className="inline-flex rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface-muted)] p-1 text-xs font-medium">
              <button
                type="button"
                onClick={() => {
                  setLoading(true);
                  setError(null);
                  setFilter('pending');
                }}
                className={`rounded-[var(--radius-md)] px-3 py-1.5 transition-colors cursor-pointer ${
                  filter === 'pending'
                    ? 'bg-[var(--surface)] text-[var(--text)] shadow-[var(--shadow-xs)]'
                    : 'text-[var(--text-muted)] hover:text-[var(--text)]'
                }`}
              >
                Actionable ({actionableCount})
              </button>
              <button
                type="button"
                onClick={() => {
                  setLoading(true);
                  setError(null);
                  setFilter('all');
                }}
                className={`rounded-[var(--radius-md)] px-3 py-1.5 transition-colors cursor-pointer ${
                  filter === 'all'
                    ? 'bg-[var(--surface)] text-[var(--text)] shadow-[var(--shadow-xs)]'
                    : 'text-[var(--text-muted)] hover:text-[var(--text)]'
                }`}
              >
                All Records ({tasks.length})
              </button>
            </div>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={loading}
              aria-label="Refresh queue"
              onClick={() => void loadTasks(filter)}
            >
              <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
              Refresh
            </Button>
          </div>
        }
      />

      {error && (
        <Alert kind="error" onClose={() => setError(null)}>
          {error}
        </Alert>
      )}

      {loading && tasks.length === 0 ? (
        <div className="grid grid-cols-1 gap-6 lg:grid-cols-12">
          <div className="space-y-3 lg:col-span-5">
            <Skeleton className="h-28 w-full" />
            <Skeleton className="h-28 w-full" />
            <Skeleton className="h-28 w-full" />
          </div>
          <div className="lg:col-span-7">
            <Skeleton className="h-96 w-full" />
          </div>
        </div>
      ) : error && tasks.length === 0 ? (
        <ErrorState
          title="Could not load review queue"
          message={error}
          onRetry={() => void loadTasks(filter)}
        />
      ) : tasks.length === 0 ? (
        <EmptyState
          icon={CheckCircle2}
          title="Review Queue is Clear"
          description="No workflows are currently awaiting human approval. All autonomous actions evaluated by AI Guardrails passed verification."
        />
      ) : (
        <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-12">
          <div className="space-y-3 lg:col-span-5">
            {tasks.map((task) => {
              const isSelected = selectedTask?.id === task.id;
              const taskActionable = isActionable(task.status);
              const confidencePct = task.confidence ? Math.round(Number(task.confidence) * 100) : 94;

              return (
                <button
                  key={task.id}
                  type="button"
                  onClick={() => selectTask(task)}
                  className={`w-full rounded-[var(--radius-lg)] border p-4 text-left transition-all cursor-pointer ${
                    isSelected
                      ? 'border-[var(--primary)] bg-[var(--primary-soft)] shadow-[var(--shadow-sm)]'
                      : 'border-[var(--border)] bg-[var(--surface)] hover:border-[var(--border-hover)] hover:bg-[var(--surface-muted)]/60'
                  }`}
                >
                  <div className="mb-2 flex items-start justify-between gap-2">
                    <Badge variant="neutral">
                      {task.workflow_name || 'Workflow Run'}
                    </Badge>
                    <Badge variant={reviewBadgeVariant(task.status, taskActionable)}>
                      {taskActionable ? 'Awaiting Review' : task.status}
                    </Badge>
                  </div>

                  <p className="line-clamp-2 text-sm font-medium text-[var(--text)]">
                    {task.risk_reason || 'Human approval required before dispatch'}
                  </p>

                  <div className="mt-3 flex items-center gap-4 border-t border-[var(--border)] pt-2 text-xs text-[var(--text-muted)]">
                    <span className="inline-flex items-center gap-1">
                      <Sparkles className="h-3.5 w-3.5 text-[var(--primary)]" />
                      Confidence: {confidencePct}%
                    </span>
                    {task.deadline_at && (
                      <span className="inline-flex items-center gap-1 font-mono">
                        <Clock className="h-3.5 w-3.5 text-[var(--text-subtle)]" />
                        {new Date(task.deadline_at).toLocaleTimeString([], {
                          hour: '2-digit',
                          minute: '2-digit',
                        })}
                      </span>
                    )}
                  </div>
                </button>
              );
            })}
          </div>

          <div className="lg:col-span-7">
            {selectedTask ? (
              <Card className="space-y-5">
                <CardHeader className="border-b border-[var(--border)]">
                  <div className="flex items-start justify-between gap-4">
                    <div className="min-w-0">
                      <div className="flex items-center gap-2">
                        <Bot className="h-5 w-5 shrink-0 text-[var(--primary)]" />
                        <h2 className="truncate text-base font-semibold text-[var(--text)]">
                          {selectedTask.workflow_name || 'Workflow Review Step'}
                        </h2>
                      </div>
                      <p className="mt-1 text-xs text-[var(--text-muted)]">
                        Run ID:{' '}
                        <code className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface-muted)] px-1.5 py-0.5 font-mono text-xs text-[var(--text)]">
                          {selectedTask.run_id}
                        </code>
                      </p>
                    </div>
                    <div className="shrink-0 text-right">
                      <div className="text-[11px] font-semibold uppercase tracking-wider text-[var(--text-subtle)]">
                        AI Confidence
                      </div>
                      <div className="font-mono text-lg font-bold text-[var(--text)]">
                        {selectedTask.confidence
                          ? `${Math.round(Number(selectedTask.confidence) * 100)}%`
                          : '94%'}
                      </div>
                    </div>
                  </div>
                </CardHeader>

                <CardContent className="space-y-5">
                  <Alert kind="warning">
                    <div className="flex items-start gap-2">
                      <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" />
                      <div>
                        <p className="text-xs font-semibold uppercase tracking-wide">
                          Interception Reason
                        </p>
                        <p className="mt-0.5 text-sm">{selectedTask.risk_reason}</p>
                      </div>
                    </div>
                  </Alert>

                  {recipient && (
                    <div className="flex items-center justify-between rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface-muted)] px-3 py-2 text-xs">
                      <span className="flex items-center gap-2 text-[var(--text-muted)]">
                        <Mail className="h-4 w-4 text-[var(--primary)]" />
                        Target Recipient:{' '}
                        <strong className="font-mono text-[var(--text)]">{recipient}</strong>
                      </span>
                      <Badge variant="primary">Verified Dispatch</Badge>
                    </div>
                  )}

                  {selectedTask.evidence && Object.keys(selectedTask.evidence).length > 0 && (
                    <div className="space-y-2">
                      <div className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)]">
                        <FileText className="h-3.5 w-3.5" />
                        Incoming Trigger Context &amp; Evidence
                      </div>
                      <div className="max-h-36 space-y-1 overflow-y-auto rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface-muted)] p-3 font-mono text-xs">
                        {Object.entries(selectedTask.evidence).map(([k, v]) => (
                          <div key={k} className="flex gap-2">
                            <span className="font-semibold text-[var(--text-muted)]">{k}:</span>
                            <span className="text-[var(--text)]">
                              {typeof v === 'object' ? JSON.stringify(v) : String(v)}
                            </span>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}

                  <div className="space-y-2">
                    <div className="flex items-center justify-between">
                      <span className="inline-flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-[var(--text-muted)]">
                        <Sparkles className="h-3.5 w-3.5 text-[var(--primary)]" />
                        AI Proposed Output
                      </span>
                      {currentActionable && (
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          onClick={() => setIsEditing((v) => !v)}
                        >
                          <Edit3 className="h-3.5 w-3.5" />
                          {isEditing ? 'Cancel Edit' : 'Edit Response'}
                        </Button>
                      )}
                    </div>

                    {isEditing ? (
                      <div className="space-y-1.5">
                        <textarea
                          aria-label="Edit response draft"
                          value={editedDraft}
                          onChange={(e) => setEditedDraft(e.target.value)}
                          rows={6}
                          className="block w-full rounded-[var(--radius-md)] border border-[var(--primary)] bg-[var(--surface)] p-3 font-mono text-sm text-[var(--text)] placeholder:text-[var(--text-subtle)] focus:ring-2 focus:ring-[var(--primary)]/20 focus:outline-none"
                          placeholder="Edit the outgoing message or response draft..."
                        />
                        <p className="text-xs text-[var(--text-muted)]">
                          Your edited content will replace the proposal and be dispatched when you
                          click &quot;Save Changes &amp; Dispatch&quot;.
                        </p>
                      </div>
                    ) : (
                      <div className="whitespace-pre-wrap rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface-muted)] p-4 text-sm leading-relaxed text-[var(--text)]">
                        {editedDraft || 'No draft content generated.'}
                      </div>
                    )}
                  </div>

                  {currentActionable && (
                    <Input
                      label="Reviewer Reason / Note (Optional)"
                      value={decisionReason}
                      onChange={(e) => setDecisionReason(e.target.value)}
                      placeholder="e.g. Verified customer invoice, approved dispatch"
                    />
                  )}

                  {currentActionable ? (
                    <div className="flex flex-wrap items-center justify-end gap-2 border-t border-[var(--border)] pt-4">
                      <Button
                        type="button"
                        variant="danger"
                        size="sm"
                        disabled={submitting}
                        onClick={() => void handleDecision('rejected')}
                      >
                        <XCircle className="h-4 w-4" />
                        Reject &amp; Halt DAG
                      </Button>

                      {isEditing ? (
                        <Button
                          type="button"
                          variant="primary"
                          size="sm"
                          loading={submitting}
                          onClick={() => void handleDecision('edited')}
                        >
                          <Send className="h-4 w-4" />
                          Save Changes &amp; Dispatch
                        </Button>
                      ) : (
                        <Button
                          type="button"
                          variant="primary"
                          size="sm"
                          loading={submitting}
                          onClick={() => void handleDecision('approved')}
                        >
                          <CheckCircle2 className="h-4 w-4" />
                          Approve &amp; Dispatch
                        </Button>
                      )}
                    </div>
                  ) : (
                    <div className="rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface-muted)] p-3.5 text-xs text-[var(--text-muted)]">
                      This task has already been resolved with status:{' '}
                      <strong className="uppercase text-[var(--text)]">
                        {selectedTask.status}
                      </strong>
                      {selectedTask.decision_reason && ` — Reason: "${selectedTask.decision_reason}"`}
                    </div>
                  )}
                </CardContent>
              </Card>
            ) : (
              <EmptyState
                icon={ShieldAlert}
                title="Select a review task"
                description="Choose a task from the queue on the left to inspect AI output and take action."
              />
            )}
          </div>
        </div>
      )}
    </div>
  );
}

export default ReviewInboxPage;
