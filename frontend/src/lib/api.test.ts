import { describe, expect, it, vi } from 'vitest'

import { ApiError, apiFetch } from './api'

function respond(status: number, body: unknown) {
  return new Response(body === null ? null : JSON.stringify(body), { status })
}

describe('apiFetch', () => {
  it('returns parsed JSON and always sends cookies', async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(200, { status: 'ok' }))
    vi.stubGlobal('fetch', fetchMock)

    const data = await apiFetch<{ status: string }>('/health')

    expect(data).toEqual({ status: 'ok' })
    const [url, init] = fetchMock.mock.calls[0]
    expect(url).toBe('/api/v1/health')
    expect(init.credentials).toBe('include')
  })

  it('sends a JSON body with a content type', async () => {
    const fetchMock = vi.fn().mockResolvedValue(respond(201, { id: '1' }))
    vi.stubGlobal('fetch', fetchMock)

    await apiFetch('/tenants', { method: 'POST', json: { name: 'Acme' } })

    const init = fetchMock.mock.calls[0][1]
    expect(init.body).toBe('{"name":"Acme"}')
    expect(new Headers(init.headers).get('Content-Type')).toBe('application/json')
  })

  it('adds the tenant header only when a tenant is given', async () => {
    const fetchMock = vi.fn().mockImplementation(async () => respond(200, {}))
    vi.stubGlobal('fetch', fetchMock)

    await apiFetch('/members', { tenantId: 'tenant-1' })
    await apiFetch('/me')

    expect(new Headers(fetchMock.mock.calls[0][1].headers).get('X-Tenant-ID')).toBe('tenant-1')
    expect(new Headers(fetchMock.mock.calls[1][1].headers).has('X-Tenant-ID')).toBe(false)
  })

  it('turns a string detail into an ApiError', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(respond(403, { detail: 'No access to this tenant' })))

    await expect(apiFetch('/members')).rejects.toMatchObject({
      name: 'ApiError',
      status: 403,
      message: 'No access to this tenant',
    })
  })

  it('joins validation messages from a 422 response', async () => {
    const detail = [{ msg: 'Field required' }, { msg: 'Invalid region' }]
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(respond(422, { detail })))

    await expect(apiFetch('/tenants', { method: 'POST', json: {} })).rejects.toMatchObject({
      status: 422,
      message: 'Field required; Invalid region',
    })
  })

  it('falls back to a generic message and keeps the body', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(respond(503, { status: 'degraded' })))

    const error = await apiFetch('/health/ready').catch((e: unknown) => e)

    expect(error).toBeInstanceOf(ApiError)
    expect((error as ApiError).message).toBe('Request failed (503)')
    expect((error as ApiError).body).toEqual({ status: 'degraded' })
  })

  it('reports a network failure as status 0', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))

    await expect(apiFetch('/health')).rejects.toMatchObject({ status: 0 })
  })

  it('handles an empty response body', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(null, { status: 204 })))

    await expect(apiFetch('/auth/logout', { method: 'POST' })).resolves.toBeNull()
  })
})
