import { useState } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { Building2, CheckCircle2, LogOut, MailWarning, Sparkles, UserCheck } from 'lucide-react';
import {
  Alert,
  AuthLayout,
  Button,
  EmptyState,
  ErrorState,
  Input,
  RoleBadge,
  Skeleton,
} from '../components/ui';
import { api, getErrorMessage } from '../lib/api';
import { useMe } from '../lib/hooks';
import { useInvitationInfo } from '../lib/membersApi';
import { setTenantId } from '../lib/tenant';
import type { SignupResult, Tenant } from '../lib/types';

export default function AcceptInvitePage() {
  const [params] = useSearchParams();
  const token = params.get('token');
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: me, isLoading: loadingMe } = useMe();
  const { data: inviteInfo, isLoading: loadingInfo, error: infoError } = useInvitationInfo(token);

  const [password, setPassword] = useState('');
  const [passwordError, setPasswordError] = useState<string | undefined>(undefined);
  const [authError, setAuthError] = useState<string | null>(null);
  const [acceptedTenant, setAcceptedTenant] = useState<Tenant | null>(null);

  const accept = useMutation({
    mutationFn: () => api<Tenant>('/invitations/accept', { method: 'POST', body: { token } }),
    onSuccess: async (tenant) => {
      setAcceptedTenant(tenant);
      setTenantId(tenant.id);
      await queryClient.invalidateQueries({ queryKey: ['me'] });
    },
  });

  const logout = useMutation({
    mutationFn: () => api('/auth/logout', { method: 'POST' }),
    onSuccess: async () => {
      setTenantId(null);
      await queryClient.invalidateQueries({ queryKey: ['me'] });
      navigate(`/login?from=${encodeURIComponent(`/accept-invite?token=${token}`)}`, {
        replace: true,
      });
    },
  });

  const directLoginAndAccept = useMutation({
    mutationFn: async () => {
      if (!inviteInfo?.email) return undefined;
      setAuthError(null);
      try {
        await api('/auth/login', {
          method: 'POST',
          body: { email: inviteInfo.email.toLowerCase(), password },
        });
      } catch (err: unknown) {
        try {
          await api<SignupResult>('/auth/signup', {
            method: 'POST',
            body: { email: inviteInfo.email.toLowerCase(), password },
          });
        } catch {
          throw err;
        }
      }

      return api<Tenant>('/invitations/accept', {
        method: 'POST',
        body: { token },
      });
    },
    onSuccess: async (tenant) => {
      if (tenant) {
        setAcceptedTenant(tenant);
        setTenantId(tenant.id);
        await queryClient.invalidateQueries({ queryKey: ['me'] });
      }
    },
    onError: (err: unknown) => {
      setAuthError(getErrorMessage(err));
    },
  });

  const backLink = (
    <Link to="/" className="font-medium text-[var(--primary)] hover:underline">
      Go to dashboard
    </Link>
  );

  if (!token) {
    return (
      <AuthLayout title="Invitation" footer={backLink}>
        <EmptyState
          icon={MailWarning}
          title="Missing invitation token"
          description="This invitation link is incomplete. Please open the full link from your invitation email."
          action={
            <Button variant="secondary" size="sm" onClick={() => navigate('/login')}>
              Go to log in
            </Button>
          }
        />
      </AuthLayout>
    );
  }

  if (loadingInfo || loadingMe) {
    return (
      <AuthLayout title="Verifying invitation" subtitle="Checking invitation details..." footer={backLink}>
        <div className="space-y-3" aria-label="Loading invitation">
          <Skeleton className="h-20 w-full" />
          <Skeleton className="h-9 w-full" />
        </div>
      </AuthLayout>
    );
  }

  if (infoError || !inviteInfo || !inviteInfo.is_valid) {
    const message = inviteInfo?.expired
      ? 'This invitation link has expired. Please ask your administrator to send a new invite.'
      : 'This invitation link is invalid, revoked, or has already been accepted.';
    return (
      <AuthLayout title="Invitation unavailable" footer={backLink}>
        <ErrorState title="Invalid or expired invitation" message={message} />
      </AuthLayout>
    );
  }

  if (acceptedTenant) {
    return (
      <AuthLayout
        title={`Welcome to ${acceptedTenant.name}`}
        subtitle="Your workspace membership is now active."
      >
        <div className="space-y-4">
          <Alert kind="success">
            You joined <strong>{acceptedTenant.name}</strong> as{' '}
            <span className="capitalize font-semibold">{acceptedTenant.role}</span>.
          </Alert>
          <Button className="w-full" onClick={() => navigate('/', { replace: true })}>
            Continue to workspace
          </Button>
        </div>
      </AuthLayout>
    );
  }

  const currentEmail = me?.email?.trim().toLowerCase();
  const invitedEmail = inviteInfo.email.trim().toLowerCase();
  const isMatch = Boolean(currentEmail && currentEmail === invitedEmail);
  const isMismatch = Boolean(currentEmail && currentEmail !== invitedEmail);
  const isLoggedOut = !currentEmail;

  return (
    <AuthLayout
      title={`Join ${inviteInfo.tenant_name}`}
      subtitle="You have been invited to collaborate on OpsMind."
      footer={backLink}
    >
      <div className="space-y-4">
        <div className="rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface-muted)]/60 p-4 space-y-2.5">
          <div className="flex items-center justify-between text-xs">
            <span className="text-[var(--text-muted)]">Company</span>
            <span className="flex items-center gap-1.5 font-semibold text-[var(--text)]">
              <Building2 className="h-3.5 w-3.5 text-[var(--primary)]" />
              {inviteInfo.tenant_name}
            </span>
          </div>

          <div className="flex items-center justify-between text-xs">
            <span className="text-[var(--text-muted)]">Assigned Role</span>
            <RoleBadge role={inviteInfo.role} />
          </div>

          <div className="flex items-center justify-between text-xs">
            <span className="text-[var(--text-muted)]">Invited Email</span>
            <span className="rounded-[var(--radius-sm)] border border-[var(--border)] bg-[var(--surface)] px-2 py-0.5 font-mono text-[11px] text-[var(--text)]">
              {inviteInfo.email}
            </span>
          </div>
        </div>

        {isMismatch && (
          <div className="space-y-3">
            <Alert kind="warning">
              You are signed in as <strong>{me?.email}</strong>, but this invitation was sent to{' '}
              <strong>{inviteInfo.email}</strong>.
            </Alert>
            <Button
              className="w-full"
              variant="primary"
              loading={logout.isPending}
              onClick={() => logout.mutate()}
            >
              <LogOut className="h-4 w-4" />
              Log out &amp; switch to {inviteInfo.email}
            </Button>
          </div>
        )}

        {isMatch && (
          <div className="space-y-3">
            <div className="flex items-center gap-2 rounded-[var(--radius-md)] border border-[var(--success)]/30 bg-[var(--success-soft)] p-2.5 text-xs text-[var(--text)]">
              <CheckCircle2 className="h-4 w-4 shrink-0 text-[var(--success)]" />
              <span>
                Signed in as <strong>{me?.email}</strong>.
              </span>
            </div>

            {accept.isError && <Alert kind="error">{getErrorMessage(accept.error)}</Alert>}

            <Button className="w-full" loading={accept.isPending} onClick={() => accept.mutate()}>
              <Sparkles className="h-4 w-4" />
              Accept invitation &amp; join
            </Button>
          </div>
        )}

        {isLoggedOut && (
          <div className="space-y-4">
            {authError && <Alert kind="error">{authError}</Alert>}

            <form
              noValidate
              onSubmit={(e) => {
                e.preventDefault();
                if (!password) {
                  setPasswordError('Password is required.');
                  return;
                }
                setPasswordError(undefined);
                directLoginAndAccept.mutate();
              }}
              className="space-y-3"
            >
              <p className="text-xs text-[var(--text-muted)]">
                Enter your password for <strong className="text-[var(--text)]">{inviteInfo.email}</strong>{' '}
                to accept this invitation:
              </p>

              <Input
                label="Account Password"
                type="password"
                showPasswordToggle
                required
                placeholder="Enter your password"
                value={password}
                error={passwordError}
                onChange={(e) => {
                  setPassword(e.target.value);
                  if (passwordError) setPasswordError(undefined);
                }}
              />

              <Button
                type="submit"
                className="w-full"
                loading={directLoginAndAccept.isPending}
              >
                <UserCheck className="h-4 w-4" />
                Sign in &amp; accept invitation
              </Button>
            </form>

            <div className="pt-1 text-center">
              <Link
                to={`/login?from=${encodeURIComponent(`/accept-invite?token=${token}`)}`}
                className="text-xs font-medium text-[var(--primary)] hover:underline"
              >
                Already have a different account? Go to Login
              </Link>
            </div>
          </div>
        )}
      </div>
    </AuthLayout>
  );
}
