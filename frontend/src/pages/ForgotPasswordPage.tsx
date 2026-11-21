import { useMutation } from '@tanstack/react-query';
import { useState, type FormEvent } from 'react';
import { Link } from 'react-router-dom';
import { Alert, AuthLayout, Button, Input } from '../components/ui';
import { api, getErrorMessage } from '../lib/api';
import type { MessageResult } from '../lib/types';

export default function ForgotPasswordPage() {
  const [email, setEmail] = useState('');
  const [emailError, setEmailError] = useState<string | undefined>(undefined);

  const request = useMutation({
    mutationFn: () =>
      api<MessageResult>('/auth/password/reset-request', {
        method: 'POST',
        body: { email: email.trim() },
      }),
  });

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    const trimmed = email.trim();
    if (!trimmed || !/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(trimmed)) {
      setEmailError('Enter a valid email address.');
      return;
    }
    setEmailError(undefined);
    request.mutate();
  }

  return (
    <AuthLayout
      title="Reset your password"
      subtitle="We will email you a reset link."
      footer={
        <Link to="/login" className="font-medium text-[var(--primary)] hover:underline">
          Back to log in
        </Link>
      }
    >
      {request.isSuccess ? (
        <Alert kind="success">{request.data.message}</Alert>
      ) : (
        <form noValidate onSubmit={onSubmit} className="space-y-4">
          {request.isError && <Alert kind="error">{getErrorMessage(request.error)}</Alert>}
          <Input
            label="Email"
            type="email"
            autoComplete="email"
            required
            placeholder="you@company.com"
            value={email}
            error={emailError}
            onChange={(event) => {
              setEmail(event.target.value);
              if (emailError) setEmailError(undefined);
            }}
          />
          <Button type="submit" className="w-full" loading={request.isPending}>
            Send reset link
          </Button>
        </form>
      )}
    </AuthLayout>
  );
}
