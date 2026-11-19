# OpsMind Frontend

React 19 + TypeScript + Vite + Tailwind CSS v4 frontend for OpsMind. The Vite dev server proxies `/api` to `http://127.0.0.1:8000` so browser requests share one origin and HttpOnly auth cookies work without CORS.

## Commands

```bash
npm ci
npm run dev      # http://localhost:5173
npm run lint     # oxlint (0 warnings enforced)
npm test         # vitest run
npm run build    # tsc -b && vite build
```

## Design System & Theme

- **Tokens (`src/index.css`)**: Semantic CSS variables (`--bg`, `--surface`, `--surface-muted`, `--border`, `--border-hover`, `--text`, `--text-muted`, `--text-subtle`, `--primary`, `--primary-hover`, `--primary-soft`, `--success`, `--warning`, `--danger`, `--radius-*`, `--shadow-*`) defined for `:root` (light) and `.dark` (dark).
- **Theme Switching (`src/lib/theme.tsx`, `src/lib/themeContext.ts`)**: `ThemeProvider` persists the user's light/dark selection in `localStorage` and toggles the `.dark` class on `document.documentElement`. Consume with `useTheme()`.
- **Shared Components (`src/components/ui/`)**: Accessible primitives built on Radix UI and `lucide-react` (`PageHeader`, `Card`, `Badge`, `RoleBadge`, `Button`, `Input`, `Select`, `Dialog`, `ConfirmDialog`, `Tabs`, `Table`, `Tooltip`, `ToastProvider`/`useToast`, `Skeleton`, `EmptyState`, `ErrorState`, `Alert`, `StatCard`, `AuthLayout`).
- **Accessibility & Motion**: Global `:focus-visible` outline, `aria-live="polite"` toast region, keyboard-navigable Radix dialogs/tabs/menus, `Escape` support on the mobile drawer, and `@media (prefers-reduced-motion: reduce)`.
- **Code Splitting (`src/App.tsx`)**: All route pages use `React.lazy` with `<Suspense fallback={<RouteSkeleton />}>`, keeping heavy modules such as React Flow (`@xyflow/react`) out of the initial bundle.
