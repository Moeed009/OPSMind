import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, api, getErrorMessage } from './api';

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('api', () => {
  it('parses json and sends cookies', async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({ ok: true }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(api('/current_user')).resolves.toEqual({ ok: true });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe('/current_user');
    expect(init.credentials).toBe('include');
  });

  it('sends X-Tenant-ID when a tenant is given', async () => {
    const fetchMock = vi.fn().mockResolvedValue(json({}));
    vi.stubGlobal('fetch', fetchMock);

    await api('/tenant', { tenantId: 'tenant-1' });

    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect((init.headers as Headers).get('X-Tenant-ID')).toBe('tenant-1');
  });

  it('refreshes once on 401 and retries the request', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json({ detail: 'Not authenticated' }, 401))
      .mockResolvedValueOnce(json({ authenticated: true }))
      .mockResolvedValueOnce(json({ id: '1' }));
    vi.stubGlobal('fetch', fetchMock);

    await expect(api('/current_user')).resolves.toEqual({ id: '1' });

    expect(fetchMock).toHaveBeenCalledTimes(3);
    expect((fetchMock.mock.calls[1] as [string])[0]).toBe('/auth/refresh');
  });

  it('gives up with the 401 when refresh fails', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json({ detail: 'Not authenticated' }, 401))
      .mockResolvedValueOnce(json({ detail: 'Refresh session not found' }, 401));
    vi.stubGlobal('fetch', fetchMock);

    await expect(api('/current_user')).rejects.toMatchObject({ status: 401 });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });

  it('never refreshes for /auth/ endpoints', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValue(json({ detail: 'Invalid email or password' }, 401));
    vi.stubGlobal('fetch', fetchMock);

    await expect(
      api('/auth/login', { method: 'POST', body: { email: 'a@b.c', password: 'x' } }),
    ).rejects.toThrow('Invalid email or password');
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('turns FastAPI validation errors into a readable message', async () => {
    const detail = [{ loc: ['body', 'password'], msg: 'String should have at least 12 characters' }];
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(json({ detail }, 422)));

    await expect(api('/auth/signup', { method: 'POST', body: {} })).rejects.toThrow(
      'password: String should have at least 12 characters',
    );
  });

  it('returns undefined for 204', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
    await expect(api('/auth/logout', { method: 'POST' })).resolves.toBeUndefined();
  });
});

describe('getErrorMessage', () => {
  it('uses the API message, and a friendly one for network errors', () => {
    expect(getErrorMessage(new ApiError(409, 'Company limit reached'))).toBe('Company limit reached');
    expect(getErrorMessage(new TypeError('Failed to fetch'))).toMatch(/Cannot reach the server/);
  });
});
