# OpsMind frontend

React + TypeScript + Vite + Tailwind. The dev server proxies `/api` to the backend on `http://127.0.0.1:8000`, so the browser only talks to one origin and the backend's HttpOnly auth cookies work without CORS.

```
npm ci
npm run dev      # http://localhost:5173
npm run lint
npm run build
npm test
```
