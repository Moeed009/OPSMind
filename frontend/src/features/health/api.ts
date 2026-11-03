import { ApiError, apiFetch } from '../../lib/api'

export interface Liveness {
  status: string
  app: string
  environment: string
}

export interface Readiness {
  status: string
  database: boolean
  redis: boolean
}

function isReadiness(value: unknown): value is Readiness {
  return (
    typeof value === 'object' &&
    value !== null &&
    typeof (value as Readiness).database === 'boolean' &&
    typeof (value as Readiness).redis === 'boolean'
  )
}

export function fetchLiveness(): Promise<Liveness> {
  return apiFetch<Liveness>('/health')
}

export async function fetchReadiness(): Promise<Readiness> {
  try {
    return await apiFetch<Readiness>('/health/ready')
  } catch (error) {
    if (error instanceof ApiError && error.status === 503 && isReadiness(error.body)) {
      return error.body
    }
    throw error
  }
}
