import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './api';
import type { DeptWorkflowDefinition } from './scheduleUtils';

export type DeptWorkflowStatus = 'draft' | 'in_review' | 'active' | 'paused';

export interface DeptWorkflowOut {
  id: string;
  tenant_id: string;
  department_id: string;
  name: string;
  description: string | null;
  status: DeptWorkflowStatus;
  definition: DeptWorkflowDefinition;
  version: number;
  active_version: number | null;
  created_by: string | null;
  template_key: string | null;
  review_comment: string | null;
  pause_reason: string | null;
  created_at: string;
  updated_at: string;
}

export interface TemplateConnectorReadiness {
  provider: string;
  connected: boolean;
}

export interface DeptWorkflowTemplateOut {
  id: string;
  key: string;
  department_type: string;
  name: string;
  description: string | null;
  required_connectors: string[];
  connector_status: TemplateConnectorReadiness[];
  ready: boolean;
  definition: DeptWorkflowDefinition;
}

export interface DeptWorkflowRunStepOut {
  id: string;
  run_id: string;
  node_id: string;
  node_type: string;
  status: string;
  input_preview: Record<string, unknown>;
  output: Record<string, unknown> | null;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
}

export interface DeptWorkflowRunOut {
  id: string;
  tenant_id: string;
  department_id: string | null;
  workflow_id: string | null;
  status: string;
  trigger_type: string;
  scheduled_for: string | null;
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  created_at: string;
}

export interface DeptWorkflowRunDetailOut extends DeptWorkflowRunOut {
  steps: DeptWorkflowRunStepOut[];
}

export interface DeptApprovalOut {
  id: string;
  department_id: string | null;
  run_id: string;
  node_id: string;
  required_role: string;
  status: 'pending' | 'approved' | 'rejected' | 'expired';
  comment: string | null;
  payload: Record<string, unknown>;
  expires_at: string | null;
  created_at: string;
}

export interface AiTestResponse {
  status: string;
  output: Record<string, unknown>;
  confidence: number;
  provider: string;
  model: string;
  latency_ms: number;
  input_tokens: number;
  output_tokens: number;
}

export function useAllDeptWorkflows() {
  return useQuery({
    queryKey: ['all-dept-workflows'],
    queryFn: () => api<DeptWorkflowOut[]>('/department-workflows'),
  });
}

export function useDeptWorkflows(deptId: string) {
  return useQuery({
    queryKey: ['dept-workflows', deptId],
    queryFn: () => api<DeptWorkflowOut[]>(`/departments/${deptId}/workflows`),
    enabled: Boolean(deptId),
  });
}

export function useDeptWorkflow(deptId: string, wfId: string) {
  return useQuery({
    queryKey: ['dept-workflow', deptId, wfId],
    queryFn: () => api<DeptWorkflowOut>(`/departments/${deptId}/workflows/${wfId}`),
    enabled: Boolean(deptId && wfId),
  });
}

export function useCreateDeptWorkflow(deptId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name: string; description?: string; definition?: DeptWorkflowDefinition }) =>
      api<DeptWorkflowOut>(`/departments/${deptId}/workflows`, {
        method: 'POST',
        body: {
          ...body,
          definition: body.definition ?? { trigger: { type: 'manual' }, nodes: [], edges: [] },
        },
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['dept-workflows', deptId] });
      void qc.invalidateQueries({ queryKey: ['all-dept-workflows'] });
      void qc.invalidateQueries({ queryKey: ['department-dashboard'] });
      void qc.invalidateQueries({ queryKey: ['departments'] });
    },
  });
}

export function useResumeDeptWorkflow(deptId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (workflowId: string) =>
      api<DeptWorkflowOut>(`/departments/${deptId}/workflows/${workflowId}/resume`, {
        method: 'POST',
      }),
    onSuccess: (_, workflowId) => {
      void qc.invalidateQueries({ queryKey: ['dept-workflow', deptId, workflowId] });
      void qc.invalidateQueries({ queryKey: ['dept-workflows', deptId] });
      void qc.invalidateQueries({ queryKey: ['all-dept-workflows'] });
      void qc.invalidateQueries({ queryKey: ['department-dashboard'] });
      void qc.invalidateQueries({ queryKey: ['departments'] });
    },
  });
}

export function useUpdateDeptWorkflow(deptId: string, wfId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { name?: string; description?: string; definition?: DeptWorkflowDefinition }) =>
      api<DeptWorkflowOut>(`/departments/${deptId}/workflows/${wfId}`, { method: 'PATCH', body }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['dept-workflow', deptId, wfId] });
      void qc.invalidateQueries({ queryKey: ['dept-workflows', deptId] });
    },
  });
}

export function useDeptWorkflowLifecycle(deptId: string, wfId: string) {
  const qc = useQueryClient();
  const invalidate = () => {
    void qc.invalidateQueries({ queryKey: ['dept-workflow', deptId, wfId] });
    void qc.invalidateQueries({ queryKey: ['dept-workflows', deptId] });
  };

  const submitReview = useMutation({
    mutationFn: () =>
      api<DeptWorkflowOut>(`/departments/${deptId}/workflows/${wfId}/submit-review`, {
        method: 'POST',
      }),
    onSuccess: invalidate,
  });

  const decideReview = useMutation({
    mutationFn: (body: { decision: 'approve' | 'send_back'; comment?: string }) =>
      api<DeptWorkflowOut>(`/departments/${deptId}/workflows/${wfId}/review`, {
        method: 'POST',
        body,
      }),
    onSuccess: invalidate,
  });

  const activate = useMutation({
    mutationFn: () =>
      api<DeptWorkflowOut>(`/departments/${deptId}/workflows/${wfId}/activate`, {
        method: 'POST',
      }),
    onSuccess: invalidate,
  });

  const pause = useMutation({
    mutationFn: () =>
      api<DeptWorkflowOut>(`/departments/${deptId}/workflows/${wfId}/pause`, { method: 'POST' }),
    onSuccess: invalidate,
  });

  const resume = useMutation({
    mutationFn: () =>
      api<DeptWorkflowOut>(`/departments/${deptId}/workflows/${wfId}/resume`, { method: 'POST' }),
    onSuccess: invalidate,
  });

  const runNow = useMutation({
    mutationFn: (input: Record<string, unknown> = {}) =>
      api<DeptWorkflowRunOut>(`/departments/${deptId}/workflows/${wfId}/run-now`, {
        method: 'POST',
        body: { input },
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['dept-workflow-runs', deptId, wfId] });
    },
  });

  return { submitReview, decideReview, activate, pause, resume, runNow };
}

export function useDeptWorkflowRuns(deptId: string, wfId: string) {
  return useQuery({
    queryKey: ['dept-workflow-runs', deptId, wfId],
    queryFn: () => api<DeptWorkflowRunOut[]>(`/departments/${deptId}/workflows/${wfId}/runs`),
    enabled: Boolean(deptId && wfId),
  });
}

export function useDeptRunDetail(deptId: string, runId: string | null) {
  return useQuery({
    queryKey: ['dept-run-detail', deptId, runId],
    queryFn: () => api<DeptWorkflowRunDetailOut>(`/departments/${deptId}/runs/${runId}`),
    enabled: Boolean(deptId && runId),
  });
}

export function useAllWorkflowTemplates() {
  return useQuery({
    queryKey: ['all-workflow-templates'],
    queryFn: () => api<DeptWorkflowTemplateOut[]>('/workflow-templates'),
  });
}

export function useDeptWorkflowTemplates(deptId: string, includeAll = false) {
  return useQuery({
    queryKey: ['dept-workflow-templates', deptId, includeAll],
    queryFn: () =>
      api<DeptWorkflowTemplateOut[]>(
        `/departments/${deptId}/workflow-templates${includeAll ? '?include_all=true' : ''}`,
      ),
    enabled: Boolean(deptId),
  });
}

export function useUseDeptWorkflowTemplate(deptId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (key: string) =>
      api<DeptWorkflowOut>(`/departments/${deptId}/workflow-templates/${key}/use`, {
        method: 'POST',
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['dept-workflows', deptId] });
    },
  });
}

export function useTestDeptAi(deptId: string) {
  return useMutation({
    mutationFn: (body: {
      task: 'classify' | 'summarize' | 'draft_reply' | 'extract' | 'score' | 'agent';
      input: unknown;
      prompt?: string | null;
      labels?: string[] | null;
      confidence_threshold?: number;
      provider?: 'auto' | 'groq' | 'gemini';
      model?: string | null;
    }) => api<AiTestResponse>(`/departments/${deptId}/ai/test`, { method: 'POST', body }),
  });
}

export function useDeptApprovals(deptId: string, statusFilter: 'pending' | 'all' = 'pending') {
  return useQuery({
    queryKey: ['dept-approvals', deptId, statusFilter],
    queryFn: () =>
      api<DeptApprovalOut[]>(`/departments/${deptId}/approvals?status_filter=${statusFilter}`),
    enabled: Boolean(deptId),
  });
}

export function useDecideDeptApproval(deptId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      approvalId,
      decision,
      comment,
    }: {
      approvalId: string;
      decision: 'approve' | 'reject';
      comment?: string;
    }) =>
      api<DeptApprovalOut>(`/departments/${deptId}/approvals/${approvalId}/decide`, {
        method: 'POST',
        body: { decision, comment },
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['dept-approvals', deptId] });
    },
  });
}

export interface DeptWorkflowEvalOut {
  eval_result_id: string;
  eval_set_id: string;
  workflow_version_id: string;
  score: number;
  pass_threshold: number;
  passed: boolean;
  case_results: Array<{
    index: number;
    passed: boolean;
    run_status?: string;
    reason?: string;
  }>;
}

export function useEvaluateDeptWorkflow(deptId: string, wfId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () =>
      api<DeptWorkflowEvalOut>(`/departments/${deptId}/workflows/${wfId}/eval`, {
        method: 'POST',
        body: {},
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['dept-workflow', deptId, wfId] });
    },
  });
}

export function useReplayDeptRun(deptId: string, wfId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (runId: string) =>
      api<DeptWorkflowRunOut>(`/departments/${deptId}/runs/${runId}/replay`, {
        method: 'POST',
        body: {},
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['dept-workflow-runs', deptId, wfId] });
    },
  });
}
