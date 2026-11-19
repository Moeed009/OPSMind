import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { Link, useLocation, useNavigate } from 'react-router-dom';
import { Alert, AuthLayout, Button, Input } from '../components/ui';
import { api, getErrorMessage } from '../lib/api';
import { encryptAuthPayload } from '../lib/payloadCrypto';

export default function LoginPage() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const location = useLocation();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [fieldErrors, setFieldErrors] = useState<{ email?: string; password?: string }>({});

  const login = useMutation({
    mutationFn: async () => {
      const encrypted = await encryptAuthPayload({ email: email.trim(), password });
      return api('/auth/login', { method: 'POST', body: { encrypted } });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['me'] });
      await queryClient.refetchQueries({ queryKey: ['me'] });
      const from = (location.state as { from?: string } | null)?.from;
      const target =
        typeof from === 'string' && from.startsWith('/') && !from.startsWith('//') ? from : '/';
      navigate(target, { replace: true });
    },
  });

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    const errors: { email?: string; password?: string } = {};
    const trimmedEmail = email.trim();
    if (!trimmedEmail || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmedEmail)) {
      errors.email = 'Enter a valid email address.';
    }
    if (!password) {
      errors.password = 'Password is required.';
    }
    if (errors.email || errors.password) {
      setFieldErrors(errors);
      return;
    }
    setFieldErrors({});
    login.mutate();
  }

  return (
    <AuthLayout
      title="Log in"
      subtitle="Welcome back to OpsMind."
      footer={
        <>
          New here?{' '}
          <Link
            to="/signup"
            className="font-medium text-[var(--primary)] hover:underline"
          >
            Create an account
          </Link>
        </>
      }
    >
      <form noValidate onSubmit={onSubmit} className="space-y-4">
        {login.isError && <Alert kind="error">{getErrorMessage(login.error)}</Alert>}
        <Input
          label="Email"
          type="email"
          autoComplete="email"
          required
          placeholder="you@company.com"
          value={email}
          error={fieldErrors.email}
          onChange={(event) => {
            setEmail(event.target.value);
            if (fieldErrors.email) setFieldErrors((prev) => ({ ...prev, email: undefined }));
          }}
        />
        <Input
          label="Password"
          type="password"
          showPasswordToggle
          autoComplete="current-password"
          required
          placeholder="Enter your password"
          value={password}
          error={fieldErrors.password}
          onChange={(event) => {
            setPassword(event.target.value);
            if (fieldErrors.password) setFieldErrors((prev) => ({ ...prev, password: undefined }));
          }}
        />
        <Button type="submit" className="w-full" loading={login.isPending || login.isSuccess}>
          Log in
        </Button>
        <p className="text-center text-xs">
          <Link
            to="/forgot-password"
            className="text-[var(--text-muted)] hover:text-[var(--text)] hover:underline"
          >
            Forgot your password?
          </Link>
        </p>
      </form>
    </AuthLayout>
  );
}
