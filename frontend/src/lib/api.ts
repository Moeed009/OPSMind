import { getTenantId } from './tenant';

const BASE = '';

export class ApiError extends Error {
  readonly status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
  }
}

type Json = Record<string, unknown> | unknown[];

export interface ApiOptions {
  method?: 'GET' | 'POST' | 'PATCH' | 'PUT' | 'DELETE';
  body?: Json;
  tenantId?: string;
}

function send(path: string, { method = 'GET', body, tenantId }: ApiOptions): Promise<Response> {
  const headers = new Headers({ Accept: 'application/json' });
  if (body !== undefined) headers.set('Content-Type', 'application/json');
  const tenant = tenantId ?? getTenantId();
  if (tenant) headers.set('X-Tenant-ID', tenant);
  return fetch(`${BASE}${path}`, {
    method,
    headers,
    credentials: 'include',
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

async function readError(res: Response): Promise<string> {
  try {
    const data: unknown = await res.json();
    if (data && typeof data === 'object' && 'detail' in data) {
      const detail = (data as { detail: unknown }).detail;
      if (typeof detail === 'string') return detail;
      if (Array.isArray(detail) && detail.length > 0) {
        const first = detail[0] as { loc?: unknown[]; msg?: string };
        const field = Array.isArray(first.loc)
          ? first.loc.filter((part) => part !== 'body').join('.')
          : '';
        const msg = first.msg ?? 'invalid value';
        return field ? `${field}: ${msg}` : msg;
      }
    }
  } catch {
  }
  return res.status >= 500 ? 'Server error. Please try again.' : `Request failed (${res.status})`;
}

let refreshInFlight: Promise<boolean> | null = null;

function refreshSession(): Promise<boolean> {
  refreshInFlight ??= fetch(`${BASE}/auth/refresh`, { method: 'POST', credentials: 'include' })
    .then((res) => res.ok)
    .catch(() => false)
    .finally(() => {
      refreshInFlight = null;
    });
  return refreshInFlight;
}

export async function api<T = unknown>(path: string, options: ApiOptions = {}): Promise<T> {
  let res = await send(path, options);
  if (res.status === 401 && !path.startsWith('/auth/') && (await refreshSession())) {
    res = await send(path, options);
  }
  if (!res.ok) throw new ApiError(res.status, await readError(res));
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

export function getErrorMessage(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  if (error instanceof TypeError) return 'Cannot reach the server. Check your connection.';
  return 'Something went wrong. Please try again.';
}
