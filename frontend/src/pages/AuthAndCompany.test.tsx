// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ThemeProvider } from '../lib/theme';
import { ApiError } from '../lib/api';
import TenantSwitcher from '../components/TenantSwitcher';
import LoginPage from './LoginPage';
import SignupPage from './SignupPage';
import AcceptInvitePage from './AcceptInvitePage';
import OnboardingPage from './OnboardingPage';

const mockApi = vi.fn();
const mockEncryptAuthPayload = vi.fn();
const mockUseMe = vi.fn();
const mockUseActiveTenant = vi.fn();
const mockUseInvitationInfo = vi.fn();
const mockSetTenantId = vi.fn();

vi.mock('../lib/api', async () => {
  const actual = await vi.importActual<typeof import('../lib/api')>('../lib/api');
  return {
    ...actual,
    api: (...args: unknown[]) => mockApi(...args),
  };
});

vi.mock('../lib/payloadCrypto', () => ({
  encryptAuthPayload: (...args: unknown[]) => mockEncryptAuthPayload(...args),
}));

vi.mock('../lib/hooks', () => ({
  useMe: () => mockUseMe(),
  useActiveTenant: () => mockUseActiveTenant(),
}));

vi.mock('../lib/membersApi', () => ({
  useInvitationInfo: (token: string | null) => mockUseInvitationInfo(token),
}));

vi.mock('../lib/tenant', () => ({
  setTenantId: (id: string | null) => mockSetTenantId(id),
}));

function renderPage(ui: React.ReactElement, initialPath = '/') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <ThemeProvider>
      <QueryClientProvider client={qc}>
        <MemoryRouter initialEntries={[initialPath]}>
          <Routes>
            <Route path="/" element={<div>Dashboard Home</div>} />
            <Route path="/login" element={ui} />
            <Route path="/signup" element={ui} />
            <Route path="/accept-invite" element={ui} />
            <Route path="/onboarding" element={ui} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>
    </ThemeProvider>,
  );
}

describe('Auth & Company screens', () => {
  afterEach(() => {
    cleanup();
  });

  beforeEach(() => {
    vi.clearAllMocks();
    mockEncryptAuthPayload.mockResolvedValue('encrypted-blob');
    mockUseMe.mockReturnValue({ data: null, isLoading: false });
    mockUseActiveTenant.mockReturnValue({ tenants: [], active: null });
    mockUseInvitationInfo.mockReturnValue({ data: null, isLoading: false, error: null });
  });

  it('validates login inputs, toggles password visibility, and shows backend error', async () => {
    mockApi.mockRejectedValueOnce(new ApiError(401, 'Invalid email or password'));

    renderPage(<LoginPage />, '/login');

    fireEvent.click(screen.getByRole('button', { name: 'Log in' }));
    expect(screen.getByText('Enter a valid email address.')).toBeTruthy();
    expect(screen.getByText('Password is required.')).toBeTruthy();

    const pwInput = screen.getByLabelText('Password') as HTMLInputElement;
    expect(pwInput.type).toBe('password');
    fireEvent.click(screen.getByRole('button', { name: 'Show password' }));
    expect(pwInput.type).toBe('text');
    fireEvent.click(screen.getByRole('button', { name: 'Hide password' }));
    expect(pwInput.type).toBe('password');

    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'alice@acme.com' } });
    fireEvent.change(pwInput, { target: { value: 'wrong-pass-123' } });
    fireEvent.click(screen.getByRole('button', { name: 'Log in' }));

    await waitFor(() => {
      expect(screen.getByText('Invalid email or password')).toBeTruthy();
    });
  });

  it('validates signup password length and confirmation match', () => {
    renderPage(<SignupPage />, '/signup');

    fireEvent.change(screen.getByLabelText('Email'), { target: { value: 'bob@acme.com' } });
    fireEvent.change(screen.getByLabelText('Password'), { target: { value: 'short' } });
    fireEvent.change(screen.getByLabelText('Confirm password'), { target: { value: 'mismatch' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create account' }));

    expect(screen.getByText('Password must be at least 12 characters.')).toBeTruthy();
    expect(screen.getByText('Passwords do not match.')).toBeTruthy();
  });

  it('renders AcceptInvitePage states: missing token, loading, expired, and accept success', async () => {
    // 1. Missing token
    renderPage(<AcceptInvitePage />, '/accept-invite');
    expect(screen.getByText('Missing invitation token')).toBeTruthy();

    // 2. Loading
    cleanup();
    mockUseInvitationInfo.mockReturnValue({ data: null, isLoading: true, error: null });
    renderPage(<AcceptInvitePage />, '/accept-invite?token=tok-123');
    expect(screen.getByLabelText('Loading invitation')).toBeTruthy();

    // 3. Expired
    cleanup();
    mockUseInvitationInfo.mockReturnValue({
      data: {
        id: 'inv-1',
        email: 'bob@acme.com',
        role: 'builder',
        tenant_name: 'Acme Corp',
        expires_at: '2026-01-01T00:00:00Z',
        expired: true,
        is_valid: false,
      },
      isLoading: false,
      error: null,
    });
    renderPage(<AcceptInvitePage />, '/accept-invite?token=tok-123');
    expect(screen.getByText('Invalid or expired invitation')).toBeTruthy();

    // 4. Valid & matching user -> accept succeeds
    cleanup();
    mockUseMe.mockReturnValue({
      data: { id: 'u-2', email: 'bob@acme.com', full_name: 'Bob', tenants: [] },
      isLoading: false,
    });
    mockUseInvitationInfo.mockReturnValue({
      data: {
        id: 'inv-1',
        email: 'bob@acme.com',
        role: 'builder',
        tenant_name: 'Acme Corp',
        expires_at: '2026-12-31T00:00:00Z',
        expired: false,
        is_valid: true,
      },
      isLoading: false,
      error: null,
    });
    mockApi.mockResolvedValueOnce({
      id: 't-1',
      name: 'Acme Corp',
      slug: 'acme',
      region: 'eu',
      plan: 'free',
      role: 'builder',
    });

    renderPage(<AcceptInvitePage />, '/accept-invite?token=tok-123');
    fireEvent.click(screen.getByRole('button', { name: /Accept invitation/i }));

    await waitFor(() => {
      expect(screen.getByText('Welcome to Acme Corp')).toBeTruthy();
    });
    expect(mockSetTenantId).toHaveBeenCalledWith('t-1');
  });

  it('validates and submits company creation on OnboardingPage and handles TenantSwitcher', async () => {
    mockApi.mockResolvedValueOnce({
      id: 't-new',
      name: 'Acme Ops',
      slug: 'acme-ops',
      region: 'eu',
      plan: 'free',
      role: 'owner',
    });

    renderPage(<OnboardingPage />, '/onboarding');
    fireEvent.click(screen.getByRole('button', { name: 'Create workspace' }));
    expect(screen.getByText('Company name must be at least 2 characters.')).toBeTruthy();

    fireEvent.change(screen.getByLabelText('Company name'), { target: { value: 'Acme Ops' } });
    fireEvent.click(screen.getByRole('button', { name: 'Create workspace' }));

    await waitFor(() => {
      expect(mockApi).toHaveBeenCalledWith('/tenants', {
        method: 'POST',
        body: { name: 'Acme Ops', region: 'eu' },
      });
    });
    expect(mockSetTenantId).toHaveBeenCalledWith('t-new');

    // TenantSwitcher empty state & multi-tenant switching
    cleanup();
    mockUseActiveTenant.mockReturnValue({ tenants: [], active: null });
    render(
      <ThemeProvider>
        <TenantSwitcher />
      </ThemeProvider>,
    );
    expect(screen.getByText('No workspace')).toBeTruthy();

    cleanup();
    const t1 = { id: 't-1', name: 'Acme One', slug: 'acme-1', region: 'eu', plan: 'free', role: 'owner' };
    const t2 = { id: 't-2', name: 'Acme Two', slug: 'acme-2', region: 'us', plan: 'pro', role: 'admin' };
    mockUseActiveTenant.mockReturnValue({ tenants: [t1, t2], active: t1 });
    render(
      <ThemeProvider>
        <TenantSwitcher />
      </ThemeProvider>,
    );
    fireEvent.click(screen.getByRole('button', { name: /Acme One/i }));
    fireEvent.click(screen.getByRole('option', { name: /Acme Two/i }));
    expect(mockSetTenantId).toHaveBeenCalledWith('t-2');
  });
});
