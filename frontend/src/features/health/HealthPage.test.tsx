import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'

import { HealthPage } from './HealthPage'

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } })
  return render(
    <QueryClientProvider client={client}>
      <HealthPage />
    </QueryClientProvider>,
  )
}

function mockBackend(ready: { status: number; body: unknown }) {
  vi.stubGlobal(
    'fetch',
    vi.fn().mockImplementation(async (url: string) => {
      if (url.endsWith('/health/ready')) {
        return new Response(JSON.stringify(ready.body), { status: ready.status })
      }
      return new Response(JSON.stringify({ status: 'ok', app: 'OpsMind', environment: 'development' }))
    }),
  )
}

describe('HealthPage', () => {
  it('shows every dependency as up when the backend is ready', async () => {
    mockBackend({ status: 200, body: { status: 'ready', database: true, redis: true } })
    renderPage()

    expect(await screen.findByText('Backend status')).toBeInTheDocument()
    expect(screen.getAllByText('Up')).toHaveLength(3)
  })

  it('shows a degraded dependency instead of an error page', async () => {
    mockBackend({ status: 503, body: { status: 'degraded', database: true, redis: false } })
    renderPage()

    expect(await screen.findByText('Backend status')).toBeInTheDocument()
    expect(screen.getAllByText('Up')).toHaveLength(2)
    expect(screen.getByText('Down')).toBeInTheDocument()
  })

  it('shows an alert when the backend is unreachable', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')))
    renderPage()

    expect(await screen.findByRole('alert')).toHaveTextContent('Cannot reach the server')
  })
})
