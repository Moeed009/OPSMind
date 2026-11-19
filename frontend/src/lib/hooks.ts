import { useQuery } from '@tanstack/react-query';
import { useEffect, useSyncExternalStore } from 'react';
import { api } from './api';
import { getTenantId, setTenantId, subscribeTenant } from './tenant';
import type { Me, Tenant } from './types';

export function useMe() {
  return useQuery({
    queryKey: ['me'],
    queryFn: () => api<Me>('/current_user'),
    retry: false,
    staleTime: 60_000,
  });
}

export function useActiveTenant() {
  const { data: me } = useMe();
  const stored = useSyncExternalStore(subscribeTenant, getTenantId, () => null);
  const tenants = me?.tenants ?? [];
  const active = tenants.find((tenant) => tenant.id === stored) ?? tenants[0] ?? null;

  useEffect(() => {
    if (active && active.id !== stored) setTenantId(active.id);
  }, [active, stored]);

  return { tenants, active };
}

export function useCurrentTenant(tenantId: string | undefined) {
  return useQuery({
    queryKey: ['tenant', tenantId],
    queryFn: () => api<Tenant>('/tenant', { tenantId }),
    enabled: Boolean(tenantId),
  });
}
