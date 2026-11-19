const KEY = 'opsmind.tenantId';
const listeners = new Set<() => void>();

export function getTenantId(): string | null {
  try {
    return localStorage.getItem(KEY);
  } catch {
    return null;
  }
}

export function setTenantId(id: string | null): void {
  try {
    if (id) localStorage.setItem(KEY, id);
    else localStorage.removeItem(KEY);
  } catch {
  }
  listeners.forEach((listener) => listener());
}

export function subscribeTenant(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}
