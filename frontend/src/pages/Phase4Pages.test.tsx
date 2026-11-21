// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ThemeProvider } from '../lib/theme';
import { ToastProvider, TooltipProvider } from '../components/ui';
import ReviewInboxPage from './ReviewInboxPage';
import SettingsPage from './SettingsPage';

const mockUseActiveTenant = vi.fn();
const mockFetchReviews = vi.fn();
const mockDecideReview = vi.fn();

vi.mock('../lib/hooks', () => ({
  useActiveTenant: () => mockUseActiveTenant(),
  useMe: () => ({ data: { id: 'u-1', email: 'op@acme.com', tenants: [] } }),
}));

vi.mock('../lib/reviewsApi', () => ({
  fetchReviews: (...args: unknown[]) => mockFetchReviews(...args),
  decideReview: (...args: unknown[]) => mockDecideReview(...args),
}));

vi.mock('../lib/deptWorkflowsApi', () => ({
  useAllWorkflowTemplates: () => ({ data: [] }),
}));

vi.mock('../lib/departmentsApi', () => ({
  useDepartments: () => ({ data: [] }),
}));

function renderWithProviders(ui: React.ReactElement) {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <ThemeProvider>
      <TooltipProvider>
        <ToastProvider>
          <QueryClientProvider client={qc}>
            <MemoryRouter>{ui}</MemoryRouter>
          </QueryClientProvider>
        </ToastProvider>
      </TooltipProvider>
    </ThemeProvider>,
  );
}

const sampleTask = {
  id: 'rev-1',
  run_id: 'run-12345678',
  workflow_name: 'Billing Escalation',
  status: 'open',
  risk_reason: 'Refund exceeds $500 policy threshold',
  confidence: 0.91,
  proposal: { content: 'Approved $650 refund for invoice #4029', to: 'customer@example.com' },
  evidence: { amount: 650, customer: 'Acme Buyer' },
  deadline_at: '2026-11-17T12:00:00Z',
  decision_reason: null,
};

describe('Phase 4 Pages', () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    vi.clearAllMocks();
  });

  it('approves a review task in ReviewInboxPage and removes it from pending queue', async () => {
    mockFetchReviews.mockResolvedValue([sampleTask]);
    mockDecideReview.mockResolvedValue({ ...sampleTask, status: 'approved' });

    renderWithProviders(<ReviewInboxPage />);

    await waitFor(() => {
      expect(screen.getAllByText('Billing Escalation').length).toBeGreaterThan(0);
    });
    expect(screen.getByText('customer@example.com')).toBeTruthy();

    fireEvent.change(screen.getByPlaceholderText(/Verified customer invoice/i), {
      target: { value: 'Looks valid' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Approve & Dispatch/i }));

    await waitFor(() => {
      expect(mockDecideReview).toHaveBeenCalledWith('rev-1', {
        decision: 'approved',
        reason: 'Looks valid',
        correction: undefined,
      });
    });
    expect(screen.getByText('Review Queue is Clear')).toBeTruthy();
  });

  it('rejects a review task in ReviewInboxPage', async () => {
    mockFetchReviews.mockResolvedValue([sampleTask]);
    mockDecideReview.mockResolvedValue({ ...sampleTask, status: 'rejected' });

    renderWithProviders(<ReviewInboxPage />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Reject & Halt DAG/i })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('button', { name: /Reject & Halt DAG/i }));

    await waitFor(() => {
      expect(mockDecideReview).toHaveBeenCalledWith('rev-1', {
        decision: 'rejected',
        reason: undefined,
        correction: undefined,
      });
    });
  });

  it('allows editing proposal and submitting edited decision in ReviewInboxPage', async () => {
    mockFetchReviews.mockResolvedValue([sampleTask]);
    mockDecideReview.mockResolvedValue({ ...sampleTask, status: 'edited' });

    renderWithProviders(<ReviewInboxPage />);

    await waitFor(() => {
      expect(screen.getByRole('button', { name: /Edit Response/i })).toBeTruthy();
    });

    fireEvent.click(screen.getByRole('button', { name: /Edit Response/i }));
    fireEvent.change(screen.getByLabelText('Edit response draft'), {
      target: { value: 'Updated $500 partial refund' },
    });
    fireEvent.click(screen.getByRole('button', { name: /Save Changes & Dispatch/i }));

    await waitFor(() => {
      expect(mockDecideReview).toHaveBeenCalledWith('rev-1', {
        decision: 'edited',
        reason: undefined,
        correction: {
          ...sampleTask.proposal,
          content: 'Updated $500 partial refund',
          draft: 'Updated $500 partial refund',
          resolution: 'Updated $500 partial refund',
        },
      });
    });
  });

  it('enforces owner/admin role gating on SettingsPage', () => {
    mockUseActiveTenant.mockReturnValue({ active: { id: 't-1', name: 'Acme', role: 'builder' } });
    renderWithProviders(<SettingsPage />);
    expect(screen.getByText('Admin Access Required')).toBeTruthy();
    cleanup();

    mockUseActiveTenant.mockReturnValue({ active: { id: 't-1', name: 'Acme', role: 'admin' } });
    renderWithProviders(<SettingsPage />);
    expect(screen.getByText('Workspace Settings')).toBeTruthy();
    expect(screen.getByText('Notification Preferences')).toBeTruthy();
  });

  it('disables approval actions for legacy read-only review tasks on ReviewInboxPage', async () => {
    mockFetchReviews.mockResolvedValue([{ ...sampleTask, is_legacy_readonly: true }]);
    renderWithProviders(<ReviewInboxPage />);
    await waitFor(() => {
      expect(screen.getAllByText('Billing Escalation').length).toBeGreaterThan(0);
    });
    expect(screen.queryByRole('button', { name: /Approve & Dispatch/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /Edit Response/i })).toBeNull();
    expect(screen.queryByRole('button', { name: /Reject/i })).toBeNull();
  });
});
