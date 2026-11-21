import { useState } from 'react';
import { CheckCircle2, ShieldAlert, XCircle } from 'lucide-react';
import { getErrorMessage } from '../lib/api';
import { useDecideDeptApproval, useDeptApprovals } from '../lib/deptWorkflowsApi';
import { Badge, Button, Card, CardContent, CardHeader, EmptyState, ErrorState, Input, Skeleton, useToast } from './ui';

export interface DeptApprovalsPanelProps {
  departmentId: string;
}

export function DeptApprovalsPanel({ departmentId }: DeptApprovalsPanelProps) {
  const { toast } = useToast();
  const approvalsQuery = useDeptApprovals(departmentId, 'pending');
  const decide = useDecideDeptApproval(departmentId);
  const [comments, setComments] = useState<Record<string, string>>({});

  if (approvalsQuery.isLoading) {
    return (
      <div className="space-y-3">
        <Skeleton className="h-28 w-full" />
        <Skeleton className="h-28 w-full" />
      </div>
    );
  }

  if (approvalsQuery.isError) {
    return (
      <ErrorState
        title="Could not load approvals"
        message={getErrorMessage(approvalsQuery.error)}
        onRetry={() => void approvalsQuery.refetch()}
      />
    );
  }

  const approvals = approvalsQuery.data ?? [];
  if (approvals.length === 0) {
    return (
      <EmptyState
        icon={ShieldAlert}
        title="No pending approvals"
        description="Workflow approval gates and low-confidence AI steps awaiting review will appear here."
      />
    );
  }

  async function handleDecision(approvalId: string, decision: 'approve' | 'reject') {
    try {
      await decide.mutateAsync({
        approvalId,
        decision,
        comment: comments[approvalId]?.trim() || undefined,
      });
      toast({
        variant: decision === 'approve' ? 'success' : 'warning',
        title: decision === 'approve' ? 'Approval granted' : 'Approval rejected',
      });
    } catch (err) {
      toast({
        variant: 'danger',
        title: 'Decision failed',
        description: getErrorMessage(err),
      });
    }
  }

  return (
    <div className="space-y-3">
      {approvals.map((item) => (
        <Card key={item.id}>
          <CardHeader className="pb-2">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2">
                <Badge variant="warning">Pending ({item.required_role})</Badge>
                <span className="font-mono text-xs text-[var(--text-muted)]">
                  Node: {item.node_id}
                </span>
              </div>
              <span className="text-xs text-[var(--text-subtle)]">
                {new Date(item.created_at).toLocaleString()}
              </span>
            </div>
          </CardHeader>
          <CardContent className="space-y-3">
            <pre className="max-h-40 overflow-y-auto rounded-[var(--radius-md)] border border-[var(--border)] bg-[var(--surface-muted)] p-3 font-mono text-xs text-[var(--text)]">
              {JSON.stringify(item.payload, null, 2)}
            </pre>
            <div className="flex flex-col gap-2 sm:flex-row sm:items-center">
              <div className="flex-1">
                <Input
                  aria-label={`Comment for ${item.node_id}`}
                  placeholder="Optional reviewer comment..."
                  value={comments[item.id] ?? ''}
                  onChange={(e) => setComments((prev) => ({ ...prev, [item.id]: e.target.value }))}
                />
              </div>
              <div className="flex items-center gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant="danger"
                  disabled={decide.isPending}
                  onClick={() => void handleDecision(item.id, 'reject')}
                >
                  <XCircle className="h-3.5 w-3.5" />
                  Reject
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="primary"
                  loading={decide.isPending}
                  onClick={() => void handleDecision(item.id, 'approve')}
                >
                  <CheckCircle2 className="h-3.5 w-3.5" />
                  Approve
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      ))}
    </div>
  );
}
