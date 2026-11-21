import { api } from './api';

export type ReviewTaskStatus =
  | 'open'
  | 'escalated'
  | 'claimed'
  | 'approved'
  | 'edited'
  | 'rejected'
  | 'pending';

export interface ReviewTask {
  id: string;
  tenant_id: string;
  run_id: string;
  run_step_id: string;
  status: ReviewTaskStatus;
  risk_reason: string;
  proposal: Record<string, any>;
  evidence: Record<string, any>;
  confidence: number | null;
  deadline_at: string;
  claimed_by: string | null;
  decided_by: string | null;
  decided_at: string | null;
  decision_reason: string | null;
  workflow_name?: string | null;
  department_id?: string | null;
  is_legacy_readonly?: boolean;
  created_at?: string;
}

export interface ReviewDecisionPayload {
  decision: 'approved' | 'edited' | 'rejected';
  reason?: string;
  correction?: Record<string, any>;
}

export async function fetchReviews(status?: string): Promise<ReviewTask[]> {
  const query = status ? `?status_filter=${encodeURIComponent(status)}` : '';
  return api<ReviewTask[]>(`/reviews${query}`);
}

export async function decideReview(id: string, payload: ReviewDecisionPayload): Promise<ReviewTask> {
  return api<ReviewTask>(`/reviews/${id}/decide`, {
    method: 'POST',
    body: payload as any,
  });
}
