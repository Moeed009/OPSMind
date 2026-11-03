import type { RouteObject } from 'react-router'

import { HealthPage } from '../features/health/HealthPage'
import { Layout } from './Layout'
import { NotFound } from './NotFound'

export const routes: RouteObject[] = [
  {
    element: <Layout />,
    children: [
      { index: true, element: <HealthPage /> },
      { path: '*', element: <NotFound /> },
    ],
  },
]
