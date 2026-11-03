export const API_BASE = '/api/v1'

export class ApiError extends Error {
  readonly status: number
  readonly body: unknown

  constructor(status: number, message: string, body: unknown = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.body = body
  }
}

export interface ApiOptions extends Omit<RequestInit, 'body' | 'credentials'> {
  json?: unknown
  tenantId?: string
}

function messageFromBody(body: unknown, fallback: string): string {
  if (typeof body === 'object' && body !== null && 'detail' in body) {
    const detail = (body as { detail: unknown }).detail
    if (typeof detail === 'string') return detail
    if (Array.isArray(detail)) {
      const messages = detail
        .map((item) =>
          typeof item === 'object' && item !== null && 'msg' in item
            ? String((item as { msg: unknown }).msg)
            : null,
        )
        .filter((message): message is string => message !== null)
      if (messages.length > 0) return messages.join('; ')
    }
  }
  return fallback
}

async function readBody(response: Response): Promise<unknown> {
  const text = await response.text()
  if (!text) return null
  try {
    return JSON.parse(text)
  } catch {
    return text
  }
}

export async function apiFetch<T>(path: string, options: ApiOptions = {}): Promise<T> {
  const { json, tenantId, headers: extraHeaders, ...init } = options
  const headers = new Headers(extraHeaders)
  headers.set('Accept', 'application/json')
  if (json !== undefined) headers.set('Content-Type', 'application/json')
  if (tenantId) headers.set('X-Tenant-ID', tenantId)

  let response: Response
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers,
      credentials: 'include',
      body: json === undefined ? undefined : JSON.stringify(json),
    })
  } catch {
    throw new ApiError(0, 'Cannot reach the server. Check your connection and try again.')
  }

  const body = await readBody(response)
  if (!response.ok) {
    throw new ApiError(
      response.status,
      messageFromBody(body, `Request failed (${response.status})`),
      body,
    )
  }
  return body as T
}
