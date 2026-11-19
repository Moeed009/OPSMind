import { api } from './api';

export type RunStatus =
  | 'pending'
  | 'running'
  | 'paused'
  | 'waiting_human'
  | 'succeeded'
  | 'failed'
  | 'cancelled';

export interface RunStep {
  id: string;
  run_id: string;
  step_index: number;
  attempt: number;
  node_id: string;
  step_type: string;
  status: string;
  input: Record<string, any>;
  output: Record<string, any> | null;
  model: string | null;
  confidence: number | null;
  prompt_tokens: number;
  completion_tokens: number;
  cost_usd: number;
  latency_ms: number | null;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface WorkflowRun {
  id: string;
  tenant_id: string;
  department_id?: string | null;
  workflow_version_id: string;
  trigger_source: string;
  status: RunStatus;
  trace_id: string;
  input: Record<string, any>;
  output: Record<string, any> | null;
  error: string | null;
  cost_usd: number;
  total_tokens: number;
  started_at: string | null;
  finished_at: string | null;
  workflow_name?: string | null;
  steps?: RunStep[];
}

export async function executeWorkflow(workflowId: string, inputData: Record<string, any> = {}): Promise<WorkflowRun> {
  return api<WorkflowRun>(`/runs/workflows/${workflowId}/execute`, {
    method: 'POST',
    body: { input_data: inputData },
  });
}

export async function fetchRuns(
  statusOrOptions?: string | { status?: string; excludeLegacy?: boolean },
): Promise<WorkflowRun[]> {
  const params = new URLSearchParams();
  if (typeof statusOrOptions === 'string') {
    if (statusOrOptions) params.set('status', statusOrOptions);
  } else if (statusOrOptions) {
    if (statusOrOptions.status) params.set('status', statusOrOptions.status);
    if (statusOrOptions.excludeLegacy) params.set('exclude_legacy', 'true');
  }
  const qs = params.toString();
  return api<WorkflowRun[]>(`/runs${qs ? `?${qs}` : ''}`);
}

export async function fetchRunDetails(runId: string): Promise<WorkflowRun> {
  return api<WorkflowRun>(`/runs/${runId}`);
}
