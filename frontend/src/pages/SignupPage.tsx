import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { Alert, AuthLayout, Button, Input } from '../components/ui';
import { api, getErrorMessage } from '../lib/api';
import { encryptAuthPayload } from '../lib/payloadCrypto';
import { MIN_PASSWORD_LENGTH, type SignupResult } from '../lib/types';

export default function SignupPage() {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [fullName, setFullName] = useState('');
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [confirm, setConfirm] = useState('');
  const [fieldErrors, setFieldErrors] = useState<{
    email?: string;
    password?: string;
    confirm?: string;
  }>({});

  const signup = useMutation({
    mutationFn: async () => {
      const encrypted = await encryptAuthPayload({
        email: email.trim(),
        password,
        ...(fullName.trim() ? { full_name: fullName.trim() } : {}),
      });
      return api<SignupResult>('/auth/signup', {
        method: 'POST',
        body: { encrypted },
      });
    },
    onSuccess: async (result) => {
      if (!result.confirmation_required) {
        await queryClient.invalidateQueries({ queryKey: ['me'] });
        await queryClient.refetchQueries({ queryKey: ['me'] });
        navigate('/', { replace: true });
      }
    },
  });

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    const errors: { email?: string; password?: string; confirm?: string } = {};
    const trimmedEmail = email.trim();
    if (!trimmedEmail || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmedEmail)) {
      errors.email = 'Enter a valid email address.';
    }
    if (password.length < MIN_PASSWORD_LENGTH) {
      errors.password = `Password must be at least ${MIN_PASSWORD_LENGTH} characters.`;
    }
    if (password !== confirm) {
      errors.confirm = 'Passwords do not match.';
    }
    if (errors.email || errors.password || errors.confirm) {
      setFieldErrors(errors);
      return;
    }
    setFieldErrors({});
    signup.mutate();
  }

  if (signup.data?.confirmation_required) {
    return (
      <AuthLayout
        title="Check your email"
        footer={
          <Link to="/login" className="font-medium text-[var(--primary)] hover:underline">
            Back to log in
          </Link>
        }
      >
        <Alert kind="success">
          We sent a confirmation link to <strong>{signup.data.user.email}</strong>. Open it, then
          log in.
        </Alert>
      </AuthLayout>
    );
  }

  return (
    <AuthLayout
      title="Create your account"
      subtitle="Then set up your company in a minute."
      footer={
        <>
          Already have an account?{' '}
          <Link to="/login" className="font-medium text-[var(--primary)] hover:underline">
            Log in
          </Link>
        </>
      }
    >
      <form noValidate onSubmit={onSubmit} className="space-y-4">
        {signup.isError && <Alert kind="error">{getErrorMessage(signup.error)}</Alert>}
        <Input
          label="Full name (optional)"
          autoComplete="name"
          placeholder="Jane Doe"
          value={fullName}
          onChange={(event) => setFullName(event.target.value)}
        />
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
          label="Confirm password"
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
        <Button type="submit" className="w-full" loading={signup.isPending || signup.isSuccess}>
          Create account
        </Button>
      </form>
    </AuthLayout>
  );
}
