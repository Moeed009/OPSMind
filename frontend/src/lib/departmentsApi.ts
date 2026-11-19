import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from './api';
import type { DepartmentRole, Role } from './types';

export type DepartmentStatus = 'active' | 'inactive' | 'archived';

export interface DepartmentTemplate {
  key: string;
  name: string;
  code: string;
  description: string;
  recommended_providers: string[];
  default_settings: Record<string, unknown>;
}

export interface Department {
  id: string;
  tenant_id: string;
  name: string;
  code: string;
  description: string | null;
  template_key: string | null;
  status: DepartmentStatus;
  settings: Record<string, unknown>;
  monthly_ai_cost_cap_usd: string | number | null;
  member_count: number;
  connection_count: number;
  workflow_count: number;
  active_workflow_count: number;
  my_effective_role?: Role | null;
  created_at: string;
  updated_at: string;
  archived_at: string | null;
}

export interface DepartmentMember {
  user_id: string;
  email: string;
  full_name: string | null;
  role: Role;
  department_role?: DepartmentRole | null;
  effective_role: Role;
  assigned_at: string;
}

export interface DepartmentConnectionItem {
  id: string;
  provider: string;
  display_name: string;
  external_account_id: string | null;
  status: string;
  department_id: string | null;
  last_error: string | null;
  last_synced_at: string | null;
}

export interface DepartmentWorkflowItem {
  id: string;
  name: string;
  description: string | null;
  department_id: string | null;
  published_version_id: string | null;
  latest_version_number: number;
  latest_version_status: string;
  pause_reason?: string | null;
  updated_at: string;
  archived_at: string | null;
}

export interface DepartmentRunItem {
  id: string;
  workflow_version_id: string;
  workflow_name: string | null;
  trigger_source: string;
  status: string;
  trace_id: string;
  total_tokens: number;
  cost_usd: string | number;
  error: string | null;
  started_at: string | null;
  finished_at: string | null;
}

export interface DepartmentOperationalMetrics {
  total_members: number;
  total_connections: number;
  healthy_connections: number;
  total_workflows: number;
  published_workflows: number;
  paused_workflows: number;
  draft_workflows: number;
  total_runs: number;
  succeeded_runs: number;
  failed_runs: number;
  waiting_review_runs: number;
  ai_requests_count: number;
  ai_prompt_tokens: number;
  ai_completion_tokens: number;
  ai_total_tokens: number;
  ai_cost_usd: string | number;
  department_monthly_cap_usd: string | number | null;
  company_monthly_cap_usd: string | number;
}

export interface DepartmentDashboard {
  department: Department;
  members: DepartmentMember[];
  connections: DepartmentConnectionItem[];
  workflows: DepartmentWorkflowItem[];
  recent_runs: DepartmentRunItem[];
  metrics: DepartmentOperationalMetrics;
}

export function useDepartmentTemplates() {
  return useQuery({
    queryKey: ['department-templates'],
    queryFn: () => api<DepartmentTemplate[]>('/departments/templates'),
  });
}

export function useDepartments(includeArchived = false) {
  return useQuery({
    queryKey: ['departments', { includeArchived }],
    queryFn: () =>
      api<Department[]>(`/departments?include_archived=${includeArchived ? 'true' : 'false'}`),
  });
}

export function useDepartment(id: string) {
  return useQuery({
    queryKey: ['department', id],
    queryFn: () => api<Department>(`/departments/${id}`),
    enabled: Boolean(id),
  });
}

export function useDepartmentDashboard(id: string) {
  return useQuery({
    queryKey: ['department-dashboard', id],
    queryFn: () => api<DepartmentDashboard>(`/departments/${id}/dashboard`),
    enabled: Boolean(id),
  });
}

export function useCreateDepartment() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      name: string;
      code?: string;
      description?: string;
      template_key?: string;
      monthly_ai_cost_cap_usd?: number | null;
    }) => api<Department>('/departments', { method: 'POST', body }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['departments'] });
    },
  });
}

export function useUpdateDepartment(departmentId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: {
      name?: string;
      description?: string;
      monthly_ai_cost_cap_usd?: number | null;
      clear_cost_cap?: boolean;
    }) => api<Department>(`/departments/${departmentId}`, { method: 'PATCH', body }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['departments'] });
      void qc.invalidateQueries({ queryKey: ['department', departmentId] });
      void qc.invalidateQueries({ queryKey: ['department-dashboard', departmentId] });
    },
  });
}

export function useChangeDepartmentStatus() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({
      departmentId,
      action,
    }: {
      departmentId: string;
      action: 'activate' | 'deactivate' | 'archive';
    }) => api<Department>(`/departments/${departmentId}/${action}`, { method: 'POST' }),
    onSuccess: (_, vars) => {
      void qc.invalidateQueries({ queryKey: ['departments'] });
      void qc.invalidateQueries({ queryKey: ['department', vars.departmentId] });
      void qc.invalidateQueries({ queryKey: ['department-dashboard', vars.departmentId] });
      void qc.invalidateQueries({ queryKey: ['workflows'] });
      void qc.invalidateQueries({ queryKey: ['members'] });
    },
  });
}

export function useAssignDepartmentMembers(departmentId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (arg: string[] | { user_ids: string[]; role?: DepartmentRole | null }) => {
      const body = Array.isArray(arg) ? { user_ids: arg } : arg;
      return api<DepartmentMember[]>(`/departments/${departmentId}/members`, {
        method: 'POST',
        body,
      });
    },
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['department-dashboard', departmentId] });
      void qc.invalidateQueries({ queryKey: ['departments'] });
      void qc.invalidateQueries({ queryKey: ['members'] });
    },
  });
}

export function useUpdateDepartmentMemberRole(departmentId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ userId, role }: { userId: string; role: DepartmentRole }) =>
      api<DepartmentMember>(`/departments/${departmentId}/members/${userId}`, {
        method: 'PATCH',
        body: { role },
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['department-dashboard', departmentId] });
      void qc.invalidateQueries({ queryKey: ['departments'] });
      void qc.invalidateQueries({ queryKey: ['members'] });
    },
  });
}

export function useRemoveDepartmentMember(departmentId: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (userId: string) =>
      api<void>(`/departments/${departmentId}/members/${userId}`, {
        method: 'DELETE',
      }),
    onSuccess: () => {
      void qc.invalidateQueries({ queryKey: ['department-dashboard', departmentId] });
      void qc.invalidateQueries({ queryKey: ['departments'] });
      void qc.invalidateQueries({ queryKey: ['members'] });
    },
  });
}
