// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ThemeProvider } from '../lib/theme';
import { ToastProvider, TooltipProvider } from '../components/ui';
import { ScheduleEditor } from '../components/ScheduleEditor';
import { DeptTemplateGallery } from '../components/DeptTemplateGallery';
import DeptWorkflowBuilderPage from './DeptWorkflowBuilderPage';
import DepartmentsPage from './DepartmentsPage';
import { validateNodeConfig } from '../lib/scheduleUtils';

if (typeof globalThis.ResizeObserver === 'undefined') {
  globalThis.ResizeObserver = class ResizeObserver {
    observe() {}
    unobserve() {}
    disconnect() {}
  };
}

const mockUseActiveTenant = vi.fn();
const mockUseDepartmentDashboard = vi.fn();
const mockUseDeptWorkflow = vi.fn();
const mockUseDeptWorkflowTemplates = vi.fn();
const mockUseUseDeptWorkflowTemplate = vi.fn();

vi.mock('../lib/hooks', () => ({
  useActiveTenant: () => mockUseActiveTenant(),
  useMe: () => ({ data: { id: 'u-1', email: 'op@acme.com', tenants: [] } }),
}));

vi.mock('../lib/departmentsApi', () => ({
  useDepartmentDashboard: () => mockUseDepartmentDashboard(),
  useDepartments: () => ({
    isLoading: false,
    error: null,
    refetch: vi.fn(),
    data: [
      { id: 'd-sup', name: 'Customer Support', code: 'SUP', status: 'ACTIVE', my_effective_role: 'admin', member_count: 1, connection_count: 1, monthly_ai_cost_cap_usd: null },
      { id: 'd-fin', name: 'Finance & Accounts', code: 'FIN', status: 'ACTIVE', my_effective_role: 'admin', member_count: 1, connection_count: 1, monthly_ai_cost_cap_usd: null },
      { id: 'd-sal', name: 'Sales & CRM', code: 'SAL', status: 'ACTIVE', my_effective_role: 'admin', member_count: 1, connection_count: 1, monthly_ai_cost_cap_usd: null },
      { id: 'd-leg', name: 'Legal & Compliance', code: 'LEG', status: 'ACTIVE', my_effective_role: 'admin', member_count: 1, connection_count: 1, monthly_ai_cost_cap_usd: null },
      { id: 'd-ops', name: 'Internal Operations', code: 'OPS', status: 'ACTIVE', my_effective_role: 'admin', member_count: 1, connection_count: 1, monthly_ai_cost_cap_usd: null },
    ],
  }),
  useDepartmentTemplates: () => ({
    data: [
      { key: 'support', name: 'Customer Support', code: 'SUP', description: '', default_ai_cost_cap_usd: 25 },
      { key: 'finance', name: 'Finance & Accounts', code: 'FIN', description: '', default_ai_cost_cap_usd: 25 },
      { key: 'sales', name: 'Sales & CRM', code: 'SAL', description: '', default_ai_cost_cap_usd: 25 },
      { key: 'legal', name: 'Legal & Compliance', code: 'LEG', description: '', default_ai_cost_cap_usd: 25 },
      { key: 'operations', name: 'Internal Operations', code: 'OPS', description: '', default_ai_cost_cap_usd: 25 },
    ],
  }),
  useCreateDepartment: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock('../lib/connectionsApi', () => ({
  useConnections: () => ({ data: [{ id: 'c-1', provider: 'gmail', status: 'CONNECTED' }] }),
}));

vi.mock('../lib/deptWorkflowsApi', () => ({
  useAllDeptWorkflows: () => ({ data: [] }),
  useDeptWorkflow: () => mockUseDeptWorkflow(),
  useUpdateDeptWorkflow: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeptWorkflowLifecycle: () => ({
    submitReview: { mutateAsync: vi.fn(), isPending: false },
    decideReview: { mutateAsync: vi.fn(), isPending: false },
    activate: { mutateAsync: vi.fn(), isPending: false },
    pause: { mutateAsync: vi.fn(), isPending: false },
    resume: { mutateAsync: vi.fn(), isPending: false },
    runNow: { mutateAsync: vi.fn(), isPending: false },
  }),
  useDeptWorkflowRuns: () => ({ data: [] }),
  useDeptRunDetail: () => ({ data: null }),
  useEvaluateDeptWorkflow: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useReplayDeptRun: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useTestDeptAi: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeptWorkflowTemplates: () => mockUseDeptWorkflowTemplates(),
  useUseDeptWorkflowTemplate: () => mockUseUseDeptWorkflowTemplate(),
}));

function renderWithProviders(ui: React.ReactElement, path = '/departments/d-1/workflows/w-1') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <ThemeProvider>
      <TooltipProvider>
        <ToastProvider>
          <QueryClientProvider client={qc}>
            <MemoryRouter initialEntries={[path]}>
              <Routes>
                <Route path="/departments/:id/workflows/:wfId" element={ui} />
                <Route path="*" element={ui} />
              </Routes>
            </MemoryRouter>
          </QueryClientProvider>
        </ToastProvider>
      </TooltipProvider>
    </ThemeProvider>,
  );
}

describe('Phase 6 Department Workflows Frontend', () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('ScheduleEditor renders summary, next 3 run times in department timezone, and updates weekly chips', () => {
    const onChange = vi.fn();
    renderWithProviders(
      <ScheduleEditor
        value={{ type: 'schedule', mode: 'daily', time: '09:00' }}
        timezone="Asia/Karachi"
        onChange={onChange}
      />,
    );

    expect(screen.getByText('Every day at 09:00 (Asia/Karachi)')).toBeTruthy();
    expect(screen.getByText(/Next 3 runs:/i)).toBeTruthy();

    cleanup();
    renderWithProviders(
      <ScheduleEditor
        value={{ type: 'schedule', mode: 'weekly', time: '09:00', weekdays: [0] }}
        timezone="Asia/Karachi"
        onChange={onChange}
      />,
    );
    expect(screen.getByText('Weekly on Mon at 09:00 (Asia/Karachi)')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'Fri' }));
    expect(onChange).toHaveBeenCalledWith(
      expect.objectContaining({ mode: 'weekly', weekdays: [0, 4] }),
    );
  });

  it('validateNodeConfig flags missing condition branches and invalid thresholds', () => {
    const missingFalse = validateNodeConfig(
      { id: 'c1', type: 'condition', expression: 'ai_1.score >= 80' },
      [{ source: 'c1', target: 'a1', label: 'true' }],
    );
    expect(missingFalse.length).toBe(1);
    expect(missingFalse[0]).toMatch(/true.*false/i);

    const validCond = validateNodeConfig(
      { id: 'c1', type: 'condition', expression: 'ai_1.score >= 80' },
      [
        { source: 'c1', target: 'a1', label: 'true' },
        { source: 'c1', target: 'a2', label: 'false' },
      ],
    );
    expect(validCond).toEqual([]);

    const badAi = validateNodeConfig(
      { id: 'ai1', type: 'classify', labels: ['billing'], confidence_threshold: 1.5 },
      [],
    );
    expect(badAi.length).toBe(1);
  });

  it('DeptTemplateGallery displays readiness badges and gates Use template button', async () => {
    const useMutate = vi.fn().mockResolvedValue({ id: 'wf-new', name: 'Email Triage' });
    const onUsed = vi.fn();
    mockUseUseDeptWorkflowTemplate.mockReturnValue({ mutateAsync: useMutate, isPending: false });
    mockUseDeptWorkflowTemplates.mockReturnValue({
      isLoading: false,
      isError: false,
      data: [
        {
          id: 't-1',
          key: 'support_email_triage',
          department_type: 'support',
          name: 'Email Triage',
          description: 'Classify support emails.',
          required_connectors: ['gmail'],
          connector_status: [{ provider: 'gmail', connected: true }],
          ready: true,
          definition: { trigger: { type: 'manual' }, nodes: [], edges: [] },
        },
        {
          id: 't-2',
          key: 'sales_weekly_pipeline',
          department_type: 'sales',
          name: 'Weekly Pipeline',
          description: 'Read sheets.',
          required_connectors: ['gmail', 'sheets'],
          connector_status: [
            { provider: 'gmail', connected: true },
            { provider: 'sheets', connected: false },
          ],
          ready: false,
          definition: { trigger: { type: 'manual' }, nodes: [], edges: [] },
        },
      ],
    });

    renderWithProviders(<DeptTemplateGallery departmentId="d-1" canUse={false} onUsed={onUsed} />);
    expect(screen.getByText('Ready')).toBeTruthy();
    expect(screen.getByText('Connector needed')).toBeTruthy();
    expect(screen.getByText(/sheets: not ready/i)).toBeTruthy();
    expect(screen.queryByRole('button', { name: /Use template/i })).toBeNull();

    cleanup();
    renderWithProviders(<DeptTemplateGallery departmentId="d-1" canUse={true} onUsed={onUsed} />);
    fireEvent.click(screen.getAllByRole('button', { name: /Use template/i })[0]);
    await waitFor(() => {
      expect(useMutate).toHaveBeenCalledWith('support_email_triage');
      expect(onUsed).toHaveBeenCalled();
    });
  });

  it('DeptWorkflowBuilderPage enforces role-based lifecycle button visibility', () => {
    const baseWf = {
      id: 'w-1',
      tenant_id: 't-1',
      department_id: 'd-1',
      name: 'Support Triage',
      description: null,
      status: 'draft',
      definition: {
        trigger: { type: 'manual' },
        nodes: [{ id: 'classify_1', type: 'classify', labels: ['billing'] }],
        edges: [],
      },
      version: 1,
      active_version: null,
      created_by: 'u-1',
      template_key: null,
      review_comment: null,
      pause_reason: null,
      created_at: '2026-11-17T00:00:00Z',
      updated_at: '2026-11-17T00:00:00Z',
    };

    // Viewer: read-only
    mockUseActiveTenant.mockReturnValue({ active: { id: 't-1', role: 'viewer' } });
    mockUseDepartmentDashboard.mockReturnValue({
      isLoading: false,
      data: { department: { id: 'd-1', my_effective_role: 'viewer', timezone: 'Asia/Karachi' } },
    });
    mockUseDeptWorkflow.mockReturnValue({ isLoading: false, isError: false, data: baseWf });
    renderWithProviders(<DeptWorkflowBuilderPage />);
    expect(screen.queryByRole('button', { name: /Save Draft/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /Submit for Review/i })).toBeNull();

    // Builder on draft: sees Save Draft & Submit for Review, not Activate
    cleanup();
    mockUseActiveTenant.mockReturnValue({ active: { id: 't-1', role: 'builder' } });
    mockUseDepartmentDashboard.mockReturnValue({
      isLoading: false,
      data: { department: { id: 'd-1', my_effective_role: 'builder', timezone: 'Asia/Karachi' } },
    });
    renderWithProviders(<DeptWorkflowBuilderPage />);
    expect(screen.getByRole('button', { name: /Save Draft/i })).toBeTruthy();
    expect(screen.getByRole('button', { name: /Submit for Review/i })).toBeTruthy();
    expect(screen.queryByRole('button', { name: /^Activate$/i })).toBeNull();

    // Admin on in_review: sees Activate
    cleanup();
    mockUseActiveTenant.mockReturnValue({ active: { id: 't-1', role: 'admin' } });
    mockUseDepartmentDashboard.mockReturnValue({
      isLoading: false,
      data: { department: { id: 'd-1', my_effective_role: 'admin', timezone: 'Asia/Karachi' } },
    });
    mockUseDeptWorkflow.mockReturnValue({
      isLoading: false,
      isError: false,
      data: { ...baseWf, status: 'in_review' },
    });
    renderWithProviders(<DeptWorkflowBuilderPage />);
    expect(screen.getByRole('button', { name: /^Activate$/i })).toBeTruthy();
  });

  it('DeptWorkflowBuilderPage renders Trigger + 8 canvas node types (9 total) in palette', () => {
    mockUseActiveTenant.mockReturnValue({ active: { id: 't-1', role: 'builder' } });
    mockUseDepartmentDashboard.mockReturnValue({
      isLoading: false,
      data: { department: { id: 'd-1', my_effective_role: 'builder', timezone: 'Asia/Karachi' } },
    });
    mockUseDeptWorkflow.mockReturnValue({
      isLoading: false,
      isError: false,
      data: {
        id: 'w-1',
        tenant_id: 't-1',
        department_id: 'd-1',
        name: 'Builder 9 Nodes',
        description: null,
        status: 'draft',
        definition: { trigger: { type: 'manual' }, nodes: [], edges: [] },
        version: 1,
        active_version: null,
        created_by: 'u-1',
        template_key: null,
        review_comment: null,
        pause_reason: null,
        created_at: '2026-11-19T00:00:00Z',
        updated_at: '2026-11-19T00:00:00Z',
      },
    });
    renderWithProviders(<DeptWorkflowBuilderPage />);
    expect(screen.getByRole('button', { name: /Trigger/i })).toBeTruthy();
    for (const label of ['Classify', 'Retrieve', 'Extract', 'Agent', 'Condition', 'Approval', 'Action', 'Wait']) {
      expect(screen.getAllByText(label).length).toBeGreaterThan(0);
    }
  });

  it('DepartmentsPage displays exactly the 5 approved departments', () => {
    mockUseActiveTenant.mockReturnValue({ active: { id: 't-1', role: 'owner' } });
    renderWithProviders(<DepartmentsPage />);
    for (const deptName of [
      'Customer Support',
      'Finance & Accounts',
      'Sales & CRM',
      'Legal & Compliance',
      'Internal Operations',
    ]) {
      expect(screen.getByText(deptName)).toBeTruthy();
    }
    expect(screen.queryByText(/Human Resources/i)).toBeNull();
    expect(screen.queryByText(/Marketing/i)).toBeNull();
  });
});
