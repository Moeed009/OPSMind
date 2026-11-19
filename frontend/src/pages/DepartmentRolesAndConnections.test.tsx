// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi, beforeEach } from 'vitest';
import { cleanup, render, screen, fireEvent, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { TooltipProvider, ToastProvider } from '../components/ui';
import { ThemeProvider } from '../lib/theme';
import { ApiError } from '../lib/api';
import DepartmentDetailPage from './DepartmentDetailPage';
import ConnectionsPage from './ConnectionsPage';
import DepartmentsPage from './DepartmentsPage';
import DashboardPage from './DashboardPage';
import MembersPage from './MembersPage';

const mockApi = vi.fn();
const mockUseActiveTenant = vi.fn();
const mockUseDepartmentDashboard = vi.fn();
const mockUseUpdateDepartmentMemberRole = vi.fn();
const mockUseAssignDepartmentMembers = vi.fn();
const mockUseMembers = vi.fn();
const mockUseInvitations = vi.fn();
const mockUseDepartments = vi.fn();
const mockUseConnections = vi.fn();
const mockUseDirectConnect = vi.fn();
const mockUseRevoke = vi.fn();
const mockUseResumeWorkflow = vi.fn();
const mockUseWorkflows = vi.fn();

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api');
  return {
    ...actual,
    api: (...args: unknown[]) => mockApi(...args),
  };
});

vi.mock('../lib/hooks', () => ({
  useActiveTenant: () => mockUseActiveTenant(),
  useCurrentTenant: () => ({ id: 't-1', name: 'Acme', region: 'eu', plan: 'pro' }),
  useMe: () => ({ data: { id: 'u-1', email: 'test@acme.com', tenants: [] } }),
}));

vi.mock('../lib/membersApi', () => ({
  useMembers: () => mockUseMembers(),
  useInvitations: () => mockUseInvitations(),
  useChangeMemberRole: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRemoveMember: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useCreateInvitation: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRevokeInvitation: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock('../lib/departmentsApi', () => ({
  useDepartments: () => mockUseDepartments(),
  useDepartmentTemplates: () => ({ data: [] }),
  useCreateDepartment: () => ({ mutateAsync: vi.fn(), isPending: false, isError: false, error: null }),
  useDepartmentDashboard: () => mockUseDepartmentDashboard(),
  useUpdateDepartment: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useChangeDepartmentStatus: () => ({ mutate: vi.fn(), isPending: false }),
  useAssignDepartmentMembers: () => mockUseAssignDepartmentMembers(),
  useUpdateDepartmentMemberRole: () => mockUseUpdateDepartmentMemberRole(),
  useRemoveDepartmentMember: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock('../lib/connectionsApi', () => ({
  useConnections: () => mockUseConnections(),
  useAuthorize: () => ({ mutateAsync: vi.fn(), isPending: false, isError: false, error: null }),
  useDirectConnect: () => mockUseDirectConnect(),
  usePoll: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useRevoke: () => mockUseRevoke(),
  useDeleteConnection: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

vi.mock('../lib/deptWorkflowsApi', () => ({
  useAllDeptWorkflows: () => mockUseWorkflows(),
  useCreateDeptWorkflow: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useResumeDeptWorkflow: () => mockUseResumeWorkflow(),
  useDeptApprovals: () => ({ data: [], isLoading: false }),
  useDecideDeptApproval: () => ({ mutateAsync: vi.fn(), isPending: false }),
  useDeptWorkflowTemplates: () => ({ data: [], isLoading: false }),
  useUseDeptWorkflowTemplate: () => ({ mutateAsync: vi.fn(), isPending: false }),
}));

function renderWithProviders(ui: React.ReactElement, initialPath = '/departments/dept-1') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <ThemeProvider>
      <TooltipProvider>
        <ToastProvider>
          <QueryClientProvider client={qc}>
            <MemoryRouter initialEntries={[initialPath]}>
              <Routes>
                <Route path="/dashboard" element={ui} />
                <Route path="/departments" element={ui} />
                <Route path="/departments/:id" element={ui} />
                <Route path="/connectors" element={ui} />
                <Route path="/members" element={ui} />
              </Routes>
            </MemoryRouter>
          </QueryClientProvider>
        </ToastProvider>
      </TooltipProvider>
    </ThemeProvider>,
  );
}

const baseDashboard = {
  department: {
    id: 'dept-1',
    tenant_id: 't-1',
    name: 'Human Resources',
    code: 'hr',
    description: 'HR ops',
    template_key: 'hr',
    status: 'active',
    settings: {},
    monthly_ai_cost_cap_usd: null,
    member_count: 1,
    connection_count: 0,
    workflow_count: 0,
    active_workflow_count: 0,
    my_effective_role: 'admin',
    created_at: '2026-11-17T00:00:00Z',
    updated_at: '2026-11-17T00:00:00Z',
    archived_at: null,
  },
  members: [
    {
      user_id: 'u-1',
      email: 'alice@acme.com',
      full_name: 'Alice Admin',
      role: 'builder',
      department_role: 'admin',
      effective_role: 'admin',
      assigned_at: '2026-11-17T00:00:00Z',
    },
  ],
  connections: [],
  workflows: [],
  recent_runs: [],
  metrics: {
    total_members: 1,
    total_connections: 0,
    healthy_connections: 0,
    total_workflows: 0,
    published_workflows: 0,
    paused_workflows: 0,
    draft_workflows: 0,
    total_runs: 0,
    succeeded_runs: 0,
    failed_runs: 0,
    waiting_review_runs: 0,
    ai_requests_count: 0,
    ai_prompt_tokens: 0,
    ai_completion_tokens: 0,
    ai_total_tokens: 0,
    ai_cost_usd: '0.000000',
    department_monthly_cap_usd: null,
    company_monthly_cap_usd: '5.0000',
  },
};

describe('Department roles & Connections UI', () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    mockUseMembers.mockReturnValue({
      data: [
        {
          user_id: 'u-2',
          email: 'bob@acme.com',
          full_name: 'Bob Unassigned',
          role: 'builder',
          joined_at: '2026-11-17T00:00:00Z',
        },
      ],
    });
    mockUseAssignDepartmentMembers.mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
      isError: false,
      error: null,
    });
    mockUseUpdateDepartmentMemberRole.mockReturnValue({
      mutateAsync: vi.fn(),
      isPending: false,
    });
    mockUseDirectConnect.mockReturnValue({
      mutate: vi.fn(),
      mutateAsync: vi.fn(),
      isPending: false,
      isError: false,
      error: null,
    });
    mockUseRevoke.mockReturnValue({ mutateAsync: vi.fn(), isPending: false });
    mockUseInvitations.mockReturnValue({ data: [], isLoading: false, isError: false, refetch: vi.fn() });
    mockUseResumeWorkflow.mockReturnValue({ mutateAsync: vi.fn(), isPending: false });
    mockUseWorkflows.mockReturnValue({ data: [] });
  });

  function clickTab(name: RegExp) {
    const tab = screen.getByRole('tab', { name });
    fireEvent.mouseDown(tab, { button: 0 });
    fireEvent.click(tab);
  }

  it('shows role dropdown for department admin and displays 409 last-admin error', async () => {
    mockUseActiveTenant.mockReturnValue({ active: { id: 't-1', role: 'builder' } });
    mockUseDepartmentDashboard.mockReturnValue({
      data: baseDashboard,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    mockUseUpdateDepartmentMemberRole.mockReturnValue({
      mutateAsync: vi.fn().mockRejectedValue(new ApiError(409, 'A department must keep at least one department admin.')),
      isPending: false,
    });

    renderWithProviders(<DepartmentDetailPage />);
    clickTab(/Members/i);

    const select = screen.getByLabelText('Department role for alice@acme.com') as HTMLSelectElement;
    expect(select).toBeTruthy();

    fireEvent.change(select, { target: { value: 'builder' } });
    await waitFor(() => {
      expect(screen.getByText('A department must keep at least one department admin.')).toBeTruthy();
    });
  });

  it('hides role dropdown for non-admin builder in department', () => {
    mockUseActiveTenant.mockReturnValue({ active: { id: 't-1', role: 'builder' } });
    mockUseDepartmentDashboard.mockReturnValue({
      data: {
        ...baseDashboard,
        department: { ...baseDashboard.department, my_effective_role: 'builder' },
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });

    renderWithProviders(<DepartmentDetailPage />);
    clickTab(/Members/i);

    expect(screen.queryByLabelText('Department role for alice@acme.com')).toBeNull();
  });

  it('displays 409 duplicate assignment error in Assign Members modal', async () => {
    mockUseActiveTenant.mockReturnValue({ active: { id: 't-1', role: 'owner' } });
    mockUseDepartmentDashboard.mockReturnValue({
      data: baseDashboard,
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    mockUseAssignDepartmentMembers.mockReturnValue({
      mutateAsync: vi.fn().mockRejectedValue(new ApiError(409, 'User is already assigned to this department.')),
      isPending: false,
      isError: true,
      error: new ApiError(409, 'User is already assigned to this department.'),
    });

    renderWithProviders(<DepartmentDetailPage />);
    clickTab(/Members/i);
    fireEvent.click(screen.getByRole('button', { name: /Assign Members/i }));

    const checkbox = screen.getByRole('checkbox');
    fireEvent.click(checkbox);
    fireEvent.click(screen.getByRole('button', { name: /Assign Selected/i }));

    await waitFor(() => {
      expect(screen.getAllByText('User is already assigned to this department.').length).toBeGreaterThan(0);
    });
  });

  it('controls Connect/Disconnect visibility and renders 409 account-already-bound error on ConnectionsPage', async () => {
    mockUseActiveTenant.mockReturnValue({ active: { id: 't-1', role: 'builder' } });
    mockUseDepartments.mockReturnValue({
      data: [
        { ...baseDashboard.department, id: 'dept-1', name: 'HR', my_effective_role: 'admin' },
        { ...baseDashboard.department, id: 'dept-2', name: 'Sales', code: 'sales', my_effective_role: 'builder' },
      ],
    });
    mockUseConnections.mockReturnValue({
      data: [
        {
          id: 'conn-1',
          provider: 'gmail',
          external_account_id: 'hr@acme.com',
          status: 'healthy',
          last_error: null,
          last_synced_at: null,
          department_id: 'dept-1',
        },
        {
          id: 'conn-2',
          provider: 'slack',
          external_account_id: 'sales-slack',
          status: 'healthy',
          last_error: null,
          last_synced_at: null,
          department_id: 'dept-2',
        },
      ],
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    mockUseDirectConnect.mockReturnValue({
      mutateAsync: vi
        .fn()
        .mockRejectedValue(
          new ApiError(409, 'This external account is already connected to another department.'),
        ),
      isPending: false,
    });

    renderWithProviders(<ConnectionsPage />, '/connectors');

    const disconnectButtons = screen.getAllByRole('button', { name: /Disconnect/i });
    expect(disconnectButtons.length).toBe(1);

    fireEvent.click(screen.getByRole('button', { name: /Connect Service/i }));
    fireEvent.change(screen.getByPlaceholderText('support@company.com'), {
      target: { value: 'sales@acme.com' },
    });
    fireEvent.click(screen.getByRole('button', { name: /^Connect$/i }));

    await waitFor(() => {
      expect(
        screen.getByText('This external account is already connected to another department.'),
      ).toBeTruthy();
    });
  });

  it('hides Create department button for non-admin on DepartmentsPage and shows paused badge', () => {
    mockUseActiveTenant.mockReturnValue({ active: { id: 't-1', name: 'Acme', role: 'builder' } });
    mockUseDepartments.mockReturnValue({
      data: [{ ...baseDashboard.department, id: 'dept-1', name: 'HR', code: 'hr' }],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    mockUseWorkflows.mockReturnValue({
      data: [{ id: 'wf-1', department_id: 'dept-1', status: 'paused' }],
    });

    renderWithProviders(<DepartmentsPage />, '/departments');
    expect(screen.queryByRole('button', { name: /Create department/i })).toBeNull();
    expect(screen.getByText(/1 paused/i)).toBeTruthy();
  });

  it('renders paused-workflows banner on DepartmentDetailPage and calls Resume', async () => {
    const resumeMutateAsync = vi.fn().mockResolvedValue({ id: 'v-1', status: 'published' });
    mockUseActiveTenant.mockReturnValue({ active: { id: 't-1', role: 'admin' } });
    mockUseResumeWorkflow.mockReturnValue({ mutateAsync: resumeMutateAsync, isPending: false });
    mockUseDepartmentDashboard.mockReturnValue({
      data: {
        ...baseDashboard,
        workflows: [
          {
            id: 'wf-1',
            name: 'HR Onboarding',
            status: 'paused',
            latest_version_status: 'paused',
            pause_reason: 'Connection revoked by admin',
            updated_at: '2026-11-17T00:00:00Z',
          },
        ],
        metrics: { ...baseDashboard.metrics, paused_workflows: 1 },
      },
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });

    renderWithProviders(<DepartmentDetailPage />);
    expect(screen.getAllByText(/Connection revoked by admin/i).length).toBeGreaterThan(0);
    fireEvent.click(screen.getAllByRole('button', { name: /Resume/i })[0]);

    await waitFor(() => {
      expect(resumeMutateAsync).toHaveBeenCalledWith('wf-1');
    });
  });

  it('shows Unassigned connections group with warning badge and Assign department + ConfirmDialog disconnect warning', () => {
    mockUseActiveTenant.mockReturnValue({ active: { id: 't-1', role: 'owner' } });
    mockUseDepartments.mockReturnValue({
      data: [{ ...baseDashboard.department, id: 'dept-1', name: 'HR', my_effective_role: 'admin' }],
    });
    mockUseConnections.mockReturnValue({
      data: [
        {
          id: 'conn-legacy',
          provider: 'gmail',
          external_account_id: 'legacy@acme.com',
          status: 'healthy',
          last_error: null,
          last_synced_at: null,
          department_id: null,
        },
      ],
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });

    renderWithProviders(<ConnectionsPage />, '/connectors');
    expect(screen.getAllByText('Unassigned').length).toBeGreaterThan(0);
    expect(screen.getByRole('button', { name: /Assign department/i })).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: /Disconnect/i }));
    expect(screen.getByText(/will pause any published department workflows/i)).toBeTruthy();
  });

  it('opens Assign to department dialog on MembersPage and posts to /departments/{id}/members', async () => {
    mockUseActiveTenant.mockReturnValue({ active: { id: 't-1', name: 'Acme', role: 'owner' } });
    mockUseDepartments.mockReturnValue({
      data: [{ ...baseDashboard.department, id: 'dept-1', name: 'HR', code: 'hr' }],
    });
    mockUseMembers.mockReturnValue({
      data: [
        {
          user_id: 'u-2',
          email: 'bob@acme.com',
          full_name: 'Bob Builder',
          role: 'builder',
          joined_at: '2026-11-17T00:00:00Z',
          departments: [],
        },
      ],
      isLoading: false,
      isError: false,
      error: null,
      refetch: vi.fn(),
    });
    mockApi.mockResolvedValue([]);

    renderWithProviders(<MembersPage />, '/members');
    const assignBtns = screen.getAllByRole('button', { name: /Assign to department/i });
    fireEvent.click(assignBtns[0]);

    fireEvent.click(screen.getByRole('button', { name: /^Assign$/i }));

    await waitFor(() => {
      expect(mockApi).toHaveBeenCalledWith('/departments/dept-1/members', {
        method: 'POST',
        body: { user_ids: ['u-2'], role: 'builder' },
      });
    });
  });

  it('renders DashboardPage and DepartmentsPage workflow counts from deptWorkflowsApi and excludes legacy runs/reviews from DashboardPage active counts', async () => {
    mockUseActiveTenant.mockReturnValue({ active: { id: 't-1', name: 'Acme', role: 'owner' } });
    mockUseConnections.mockReturnValue({ data: [], isLoading: false });
    mockApi.mockImplementation((path: string) => {
      if (path.startsWith('/runs')) {
        return Promise.resolve([
          {
            id: 'run-dept-1',
            tenant_id: 't-1',
            department_id: 'dept-1',
            workflow_version_id: 'v-1',
            trigger_source: 'manual',
            status: 'succeeded',
            trace_id: 'tr-dept-1',
            input: {},
            output: {},
            error: null,
            cost_usd: 0,
            total_tokens: 100,
            started_at: '2026-11-19T10:00:00Z',
            finished_at: '2026-11-19T10:01:00Z',
            workflow_name: 'Support Ticket Triage',
          },
          {
            id: 'run-legacy-null',
            tenant_id: 't-1',
            department_id: null,
            workflow_version_id: 'v-leg',
            trigger_source: 'manual',
            status: 'failed',
            trace_id: 'tr-leg-null',
            input: {},
            output: null,
            error: 'Legacy failure',
            cost_usd: 0,
            total_tokens: 0,
            started_at: '2026-11-10T10:00:00Z',
            finished_at: '2026-11-10T10:01:00Z',
            workflow_name: 'Legacy Company Triage',
          },
        ]);
      }
      if (path.startsWith('/reviews')) {
        return Promise.resolve([
          {
            id: 'rev-dept-1',
            run_id: 'run-dept-1',
            department_id: 'dept-1',
            is_legacy_readonly: false,
            status: 'open',
            risk_reason: 'Dept review',
            proposal: {},
            evidence: {},
            confidence: 0.9,
          },
          {
            id: 'rev-legacy-null',
            run_id: 'run-legacy-null',
            department_id: null,
            is_legacy_readonly: true,
            status: 'open',
            risk_reason: 'Legacy review',
            proposal: {},
            evidence: {},
            confidence: 0.9,
          },
        ]);
      }
      return Promise.resolve([]);
    });
    mockUseDepartments.mockReturnValue({
      data: [
        {
          ...baseDashboard.department,
          id: 'dept-1',
          name: 'Customer Support',
          code: 'SUP',
          workflow_count: 2,
          active_workflow_count: 1,
        },
      ],
      isLoading: false,
      error: null,
      refetch: vi.fn(),
    });
    mockUseWorkflows.mockReturnValue({
      data: [
        {
          id: 'dwf-1',
          tenant_id: 't-1',
          department_id: 'dept-1',
          name: 'Support Ticket Triage',
          description: 'Triages support tickets',
          status: 'active',
          version: 2,
          active_version: 2,
          created_at: '2026-11-17T00:00:00Z',
          updated_at: '2026-11-17T00:00:00Z',
        },
        {
          id: 'dwf-2',
          tenant_id: 't-1',
          department_id: 'dept-1',
          name: 'Paused Escalation Flow',
          description: 'Paused flow',
          status: 'paused',
          version: 1,
          active_version: null,
          created_at: '2026-11-17T00:00:00Z',
          updated_at: '2026-11-17T00:00:00Z',
        },
      ],
      isLoading: false,
    });

    renderWithProviders(<DashboardPage />, '/dashboard');
    expect(screen.getByText('1 published • 1 draft')).toBeTruthy();
    expect(screen.getAllByText('Support Ticket Triage').length).toBeGreaterThan(0);

    await waitFor(() => {
      expect(screen.getByText('1 of 1 runs passed')).toBeTruthy();
      expect(screen.getByText('1 Pending')).toBeTruthy();
    });
    expect(screen.queryByText('Legacy Company Triage')).toBeNull();

    cleanup();
    renderWithProviders(<DepartmentsPage />, '/departments');
    expect(screen.getByText(/1 paused/i)).toBeTruthy();
  });
});
