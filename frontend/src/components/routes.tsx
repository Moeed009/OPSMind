import { Navigate, Outlet, useLocation } from 'react-router-dom';
import { ApiError } from '../lib/api';
import { useMe } from '../lib/hooks';
import { Button, FullPageSpinner } from './ui';

function fromOf(state: unknown): string {
  const from = (state as { from?: unknown } | null)?.from;
  return typeof from === 'string' && from.startsWith('/') && !from.startsWith('//') ? from : '/';
}

export function ProtectedRoute() {
  const location = useLocation();
  const me = useMe();

  if (me.isPending) return <FullPageSpinner />;
  if (me.error instanceof ApiError && me.error.status === 401) {
    return <Navigate to="/login" replace state={{ from: location.pathname + location.search }} />;
  }
  if (me.isError) {
    return (
      <div className="flex min-h-screen flex-col items-center justify-center gap-4 bg-[var(--bg)] p-6 text-center text-[var(--text)]">
        <p className="text-sm text-[var(--text-muted)]">We could not load your account.</p>
        <Button onClick={() => void me.refetch()}>Try again</Button>
      </div>
    );
  }
  return <Outlet />;
}

export function PublicOnlyRoute() {
  const location = useLocation();
  const me = useMe();

  if (me.isPending) return <FullPageSpinner />;
  if (me.data) return <Navigate to={fromOf(location.state)} replace />;
  return <Outlet />;
}

export function RequireCompany() {
  const { data } = useMe();
  if (data && data.tenants.length === 0) return <Navigate to="/onboarding" replace />;
  return <Outlet />;
}
