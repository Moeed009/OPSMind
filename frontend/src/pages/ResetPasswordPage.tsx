import { useMutation } from '@tanstack/react-query';
import { useEffect, useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { Alert, AuthLayout, Button, ErrorState, Input } from '../components/ui';
import { api, getErrorMessage } from '../lib/api';
import { MIN_PASSWORD_LENGTH, type MessageResult } from '../lib/types';

function readRecoveryParams() {
  const params = new URLSearchParams(window.location.hash.replace(/^#/, ''));
  return { accessToken: params.get('access_token'), linkError: params.get('error_description') };
}

export default function ResetPasswordPage() {
  const [{ accessToken, linkError }] = useState(readRecoveryParams);
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [fieldErrors, setFieldErrors] = useState<{ password?: string; confirm?: string }>({});

  useEffect(() => {
    if (window.location.hash) {
      window.history.replaceState(null, '', window.location.pathname);
    }
  }, []);

  const reset = useMutation({
    mutationFn: () =>
      api<MessageResult>('/auth/password/reset', {
        method: 'POST',
        body: { access_token: accessToken, new_password: password },
      }),
  });

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    const errors: { password?: string; confirm?: string } = {};
    if (password.length < MIN_PASSWORD_LENGTH) {
      errors.password = `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`;
    }
    if (password !== confirm) {
      errors.confirm = 'Passwords do not match.';
    }
    if (errors.password || errors.confirm) {
      setFieldErrors(errors);
      return;
    }
    setFieldErrors({});
    reset.mutate();
  }

  const loginLink = (
    <Link to="/login" className="font-medium text-[var(--primary)] hover:underline">
      Back to log in
    </Link>
  );

  if (!accessToken) {
    return (
      <AuthLayout title="Reset link problem" footer={loginLink}>
        <ErrorState
          title="Invalid or expired reset link"
          message={linkError ?? 'This reset link is invalid or has expired.'}
        />
        <p className="text-center text-xs text-[var(--text-muted)]">
          <Link to="/forgot-password" className="font-medium text-[var(--primary)] hover:underline">
            Request a new link
          </Link>
        </p>
      </AuthLayout>
    );
  }

  if (reset.isSuccess) {
    return (
      <AuthLayout title="Password updated" footer={loginLink}>
        <Alert kind="success">{reset.data.message}</Alert>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout title="Choose a new password" footer={loginLink}>
      <form noValidate onSubmit={onSubmit} className="space-y-4">
        {reset.isError && <Alert kind="error">{getErrorMessage(reset.error)}</Alert>}
        <Input
          label="New password"
          type="password"
          showPasswordToggle
          autoComplete="new-password"
          required
          hint={`At least ${MIN_PASSWORD_LENGTH} characters.`}
          value={password}
          error={fieldErrors.password}
          onChange={(event) => {
            setPassword(event.target.value);
            if (fieldErrors.password) setFieldErrors((prev) => ({ ...prev, password: undefined }));
          }}
        />
        <Input
          label="Confirm new password"
          type="password"
          showPasswordToggle
          autoComplete="new-password"
          required
          value={confirm}
          error={fieldErrors.confirm}
          onChange={(event) => {
            setConfirm(event.target.value);
            if (fieldErrors.confirm) setFieldErrors((prev) => ({ ...prev, confirm: undefined }));
          }}
        />
        <Button type="submit" className="w-full" loading={reset.isPending}>
          Update password
        </Button>
      </form>
    </AuthLayout>
  );
}
