import { useQuery } from '@tanstack/react-query'

import { fetchLiveness, fetchReadiness } from './api'

function Row({ label, ok }: { label: string; ok: boolean }) {
  return (
    <li className="flex items-center justify-between py-2">
      <span>{label}</span>
      <span className={ok ? 'font-medium text-emerald-700' : 'font-medium text-red-700'}>
        {ok ? 'Up' : 'Down'}
      </span>
    </li>
  )
}

export function HealthPage() {
  const liveness = useQuery({ queryKey: ['health'], queryFn: fetchLiveness })
  const readiness = useQuery({ queryKey: ['health', 'ready'], queryFn: fetchReadiness })

  if (liveness.isPending || readiness.isPending) {
    return <p role="status">Checking the backend...</p>
  }

  if (liveness.isError || readiness.isError) {
    const error = liveness.error ?? readiness.error
    return (
      <p role="alert" className="rounded border border-red-200 bg-red-50 p-4 text-red-800">
        {error instanceof Error ? error.message : 'Backend check failed'}
      </p>
    )
  }

  return (
    <section className="rounded border border-slate-200 bg-white p-4">
      <h2 className="text-lg font-semibold">Backend status</h2>
      <p className="text-sm text-slate-600">
        {liveness.data.app} ({liveness.data.environment})
      </p>
      <ul className="mt-3 divide-y divide-slate-100">
        <Row label="API" ok={liveness.data.status === 'ok'} />
        <Row label="Database" ok={readiness.data.database} />
        <Row label="Redis" ok={readiness.data.redis} />
      </ul>
    </section>
  )
}
