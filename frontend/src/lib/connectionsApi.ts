import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './api';

export type ConnectionStatus =
  | 'CONNECTED'
  | 'NEEDS_REAUTH'
  | 'RATE_LIMITED'
  | 'ERROR'
  | 'REVOKED';

export interface Connection {
  id: string;
  provider: string;
  external_account_id: string | null;
  status: ConnectionStatus;
  last_error: string | null;
  last_synced_at: string | null;
  department_id?: string | null;
}

export interface PollResult {
  status: ConnectionStatus;
  fetched: number;
  inserted: number;
  triggered_runs?: number;
}

export const connectionsKey = ['connections'] as const;

export function useConnections(departmentId?: string | null) {
  return useQuery({
    queryKey: departmentId ? [...connectionsKey, departmentId] : connectionsKey,
    queryFn: () =>
      api<Connection[]>(
        departmentId ? `/connections?department_id=${encodeURIComponent(departmentId)}` : '/connections',
      ),
  });
}

export function useAuthorize() {
  return useMutation({
    mutationFn: (arg: string | { provider: string; department_id?: string | null }) => {
      const provider = typeof arg === 'string' ? arg : arg.provider;
      const deptId = typeof arg === 'string' ? undefined : arg.department_id;
      const qs = deptId ? `?department_id=${encodeURIComponent(deptId)}` : '';
      return api<{ authorize_url: string }>(`/connections/oauth/start/${provider}${qs}`, {
        method: 'POST',
      });
    },
  });
}

export function useDirectConnect() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (payload: {
      provider: string;
      external_account_id: string;
      credentials: Record<string, any>;
      department_id?: string | null;
    }) =>
      api<Connection>('/connections/direct', {
        method: 'POST',
        body: payload,
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: connectionsKey });
      void qc.invalidateQueries({ queryKey: ['department-dashboard'] });
      void qc.invalidateQueries({ queryKey: ['departments'] });
    },
  });
}

export function usePoll() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api<PollResult>(`/connections/${id}/poll`, { method: 'POST' }),
    onSettled: () => qc.invalidateQueries({ queryKey: connectionsKey }),
  });
}

export interface PausedWorkflowNotice {
  workflow_id: string;
  workflow_name: string;
  version_id: string;
}

export interface DisconnectResult {
  status: string;
  paused_workflows: PausedWorkflowNotice[];
}

export function useRevoke() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api<DisconnectResult>(`/connections/${id}`, { method: 'DELETE' }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: connectionsKey });
      void qc.invalidateQueries({ queryKey: ['workflows'] });
      void qc.invalidateQueries({ queryKey: ['departments'] });
      void qc.invalidateQueries({ queryKey: ['department-dashboard'] });
    },
  });
}

export function useDeleteConnection() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) =>
      api<DisconnectResult>(`/connections/${id}?permanent=true`, { method: 'DELETE' }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: connectionsKey });
      void qc.invalidateQueries({ queryKey: ['workflows'] });
      void qc.invalidateQueries({ queryKey: ['departments'] });
      void qc.invalidateQueries({ queryKey: ['department-dashboard'] });
    },
  });
}
