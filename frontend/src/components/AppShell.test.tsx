// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { ThemeProvider } from '../lib/theme';
import { ToastProvider, TooltipProvider, ConfirmDialog, ErrorState, PageHeader, Tabs, TabsList, TabsTrigger, TabsContent, Table, TableHeader, TableBody, TableRow, TableHead, TableCell, useToast } from './ui';
import AppShell from './AppShell';

const mockUseMe = vi.fn();
const mockUseActiveTenant = vi.fn();

vi.mock('../lib/hooks', () => ({
  useMe: () => mockUseMe(),
  useActiveTenant: () => mockUseActiveTenant(),
}));

vi.mock('../lib/reviewsApi', () => ({
  fetchReviews: vi.fn().mockResolvedValue([{ id: 'r-1' }, { id: 'r-2' }]),
}));

function renderShell(initialPath = '/', defaultTheme?: 'light' | 'dark') {
  const qc = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <ThemeProvider defaultTheme={defaultTheme}>
      <TooltipProvider>
        <ToastProvider>
          <QueryClientProvider client={qc}>
            <MemoryRouter initialEntries={[initialPath]}>
              <Routes>
                <Route path="/" element={<AppShell />}>
                  <Route index element={<div>Overview Content</div>} />
                  <Route path="departments" element={<div>Departments Content</div>} />
                  <Route path="settings" element={<div>Settings Content</div>} />
                </Route>
              </Routes>
            </MemoryRouter>
          </QueryClientProvider>
        </ToastProvider>
      </TooltipProvider>
    </ThemeProvider>,
  );
}

describe('AppShell & Design System', () => {
  afterEach(() => {
    cleanup();
    window.localStorage.clear();
    document.documentElement.classList.remove('dark');
  });

  beforeEach(() => {
    vi.clearAllMocks();
    mockUseMe.mockReturnValue({
      data: { id: 'u-1', email: 'alice@acme.com', full_name: 'Alice Admin', tenants: [] },
    });
  });

  it('shows Settings nav item for owner/admin and hides it for builder/viewer', () => {
    mockUseActiveTenant.mockReturnValue({
      tenants: [{ id: 't-1', name: 'Acme Corp', slug: 'acme', region: 'us', plan: 'pro', role: 'owner' }],
      active: { id: 't-1', name: 'Acme Corp', slug: 'acme', region: 'us', plan: 'pro', role: 'owner' },
    });
    renderShell('/');
    expect(screen.getByRole('link', { name: /Settings/i })).toBeTruthy();

    cleanup();
    mockUseActiveTenant.mockReturnValue({
      tenants: [{ id: 't-1', name: 'Acme Corp', slug: 'acme', region: 'us', plan: 'pro', role: 'builder' }],
      active: { id: 't-1', name: 'Acme Corp', slug: 'acme', region: 'us', plan: 'pro', role: 'builder' },
    });
    renderShell('/');
    expect(screen.queryByRole('link', { name: /Settings/i })).toBeNull();
  });

  it('toggles and persists theme in localStorage and documentElement', () => {
    mockUseActiveTenant.mockReturnValue({
      tenants: [],
      active: { id: 't-1', name: 'Acme Corp', slug: 'acme', region: 'us', plan: 'free', role: 'admin' },
    });

    renderShell('/');
    expect(document.documentElement.classList.contains('dark')).toBe(true);
    expect(window.localStorage.getItem('opsmind_theme')).toBe('dark');

    fireEvent.click(screen.getByRole('button', { name: 'Toggle theme' }));
    expect(document.documentElement.classList.contains('dark')).toBe(false);
    expect(window.localStorage.getItem('opsmind_theme')).toBe('light');

    // Re-mounting reads persisted light theme
    cleanup();
    renderShell('/');
    expect(document.documentElement.classList.contains('dark')).toBe(false);
  });

  it('opens and closes the mobile navigation drawer', () => {
    mockUseActiveTenant.mockReturnValue({
      tenants: [],
      active: { id: 't-1', name: 'Acme Corp', slug: 'acme', region: 'us', plan: 'free', role: 'owner' },
    });

    renderShell('/');
    expect(screen.queryByRole('dialog', { name: 'Mobile navigation' })).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: 'Toggle navigation menu' }));
    const drawer = screen.getByRole('dialog', { name: 'Mobile navigation' });
    expect(drawer).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: 'Close navigation menu' }));
    expect(screen.queryByRole('dialog', { name: 'Mobile navigation' })).toBeNull();
  });

  it('persists desktop sidebar collapse state', () => {
    mockUseActiveTenant.mockReturnValue({
      tenants: [],
      active: { id: 't-1', name: 'Acme Corp', slug: 'acme', region: 'us', plan: 'free', role: 'owner' },
    });

    renderShell('/');
    fireEvent.click(screen.getByRole('button', { name: 'Collapse sidebar' }));
    expect(window.localStorage.getItem('opsmind_sidebar_collapsed')).toBe('true');
    expect(screen.getByRole('button', { name: 'Expand sidebar' })).toBeTruthy();
  });

  it('renders shared design system primitives (PageHeader, Tabs, Table, ConfirmDialog, ErrorState, Toast)', async () => {
    function Demo() {
      const { toast } = useToast();
      return (
        <div>
          <PageHeader title="Workflows" description="Manage pipelines" />
          <Tabs defaultValue="a">
            <TabsList>
              <TabsTrigger value="a">Tab A</TabsTrigger>
              <TabsTrigger value="b">Tab B</TabsTrigger>
            </TabsList>
            <TabsContent value="a">Content A</TabsContent>
            <TabsContent value="b">Content B</TabsContent>
          </Tabs>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Name</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow>
                <TableCell>Row 1</TableCell>
              </TableRow>
            </TableBody>
          </Table>
          <ErrorState message="Network timeout" onRetry={() => toast({ title: 'Retried', variant: 'success' })} />
          <ConfirmDialog
            open={false}
            onOpenChange={() => {}}
            title="Confirm"
            description="Sure?"
            onConfirm={() => {}}
          />
        </div>
      );
    }

    render(
      <ThemeProvider>
        <ToastProvider>
          <Demo />
        </ToastProvider>
      </ThemeProvider>,
    );

    expect(screen.getByText('Workflows')).toBeTruthy();
    expect(screen.getByText('Content A')).toBeTruthy();
    expect(screen.getByText('Row 1')).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: /Try again/i }));
    await waitFor(() => {
      expect(screen.getByText('Retried')).toBeTruthy();
    });
  });
});
