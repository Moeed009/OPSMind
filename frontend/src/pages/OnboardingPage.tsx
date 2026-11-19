import { useMutation, useQueryClient } from '@tanstack/react-query';
import { useEffect, useState, type FormEvent } from 'react';
import { useNavigate } from 'react-router-dom';
import { Building2, LogOut } from 'lucide-react';
import { Alert, AuthLayout, Button, Input, Select } from '../components/ui';
import { api, getErrorMessage } from '../lib/api';
import { useMe } from '../lib/hooks';
import { setTenantId } from '../lib/tenant';
import { REGION_LABELS, type Region, type Tenant } from '../lib/types';

export default function OnboardingPage() {
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const { data: me } = useMe();
  const [name, setName] = useState('');
  const [region, setRegion] = useState<Region>('eu');
  const [nameError, setNameError] = useState<string | undefined>(undefined);

  const hasCompany = (me?.tenants?.length ?? 0) > 0;

  useEffect(() => {
    if (hasCompany) {
      navigate('/', { replace: true });
    }
  }, [hasCompany, navigate]);

  const logout = useMutation({
    mutationFn: () => api('/auth/logout', { method: 'POST' }),
    onSettled: () => {
      setTenantId(null);
      queryClient.clear();
      navigate('/login', { replace: true });
    },
  });

  const create = useMutation({
    mutationFn: () =>
      api<Tenant>('/tenants', { method: 'POST', body: { name: name.trim(), region } }),
    onSuccess: async (tenant) => {
      setTenantId(tenant.id);
      await queryClient.invalidateQueries({ queryKey: ['me'] });
      navigate('/', { replace: true });
    },
  });

  function onSubmit(event: FormEvent) {
    event.preventDefault();
    const trimmed = name.trim();
    if (trimmed.length < 2) {
      setNameError('Company name must be at least 2 characters.');
      return;
    }
    setNameError(undefined);
    create.mutate();
  }

  return (
    <AuthLayout
      title="Set up your company"
      subtitle="You don't belong to a company yet. Create your first workspace to get started."
      footer={
        <button
          type="button"
          onClick={() => logout.mutate()}
          className="inline-flex items-center gap-1.5 text-xs font-medium text-[var(--text-muted)] hover:text-[var(--text)] cursor-pointer"
        >
          <LogOut className="h-3.5 w-3.5" />
          Log out{me?.email ? ` (${me.email})` : ''}
        </button>
      }
    >
      <div className="flex items-start gap-3 rounded-[var(--radius-lg)] border border-[var(--border)] bg-[var(--surface-muted)]/60 p-3.5 text-xs text-[var(--text-muted)]">
        <Building2 className="h-4 w-4 shrink-0 mt-0.5 text-[var(--primary)]" />
        <span>
          You will become the <strong className="text-[var(--text)]">Owner</strong> and can invite
          team members or create departments right after.
        </span>
      </div>

      <form noValidate onSubmit={onSubmit} className="space-y-4">
        {create.isError && <Alert kind="error">{getErrorMessage(create.error)}</Alert>}
        <Input
          label="Company name"
          required
          minLength={2}
          maxLength={200}
          placeholder="e.g. Acme Operations Corp"
          value={name}
          error={nameError}
          onChange={(event) => {
            setName(event.target.value);
            if (nameError) setNameError(undefined);
          }}
        />
        <Select
          label="Data region"
          hint="Data sovereignty boundary. Cannot be changed later."
          value={region}
          onChange={(event) => setRegion(event.target.value as Region)}
        >
          {(Object.keys(REGION_LABELS) as Region[]).map((key) => (
            <option key={key} value={key}>
              {REGION_LABELS[key]}
            </option>
          ))}
        </Select>
        <Button type="submit" className="w-full" loading={create.isPending || create.isSuccess}>
          Create workspace
        </Button>
      </form>
    </AuthLayout>
  );
}
