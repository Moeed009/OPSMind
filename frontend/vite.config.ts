/// <reference types="vitest/config" />
import fs from 'node:fs';
import type { Socket } from 'node:net';
import path from 'node:path';
import tailwindcss from '@tailwindcss/vite';
import react from '@vitejs/plugin-react';
import { defineConfig, type Plugin } from 'vite';

const BACKEND_URL = 'http://127.0.0.1:8000';
const certFile = path.resolve(import.meta.dirname, '.certs/localhost-cert.pem');
const keyFile = path.resolve(import.meta.dirname, '.certs/localhost-key.pem');
const httpsConfig =
  fs.existsSync(certFile) && fs.existsSync(keyFile)
    ? {
        key: fs.readFileSync(keyFile),
        cert: fs.readFileSync(certFile),
      }
    : undefined;

const httpToHttpsRedirect = (): Plugin => ({
  name: 'http-to-https-redirect',
  configureServer(server) {
    const activeHandles = (
      process as unknown as { _getActiveHandles?: () => Array<{ destroy?: () => void; server?: unknown }> }
    )._getActiveHandles?.();
    if (activeHandles) {
      for (const handle of activeHandles) {
        if (handle && handle.server && handle.server !== server.httpServer && typeof handle.destroy === 'function') {
          handle.destroy();
        }
      }
    }
    server.httpServer?.on(
      'tlsClientError',
      (err: Error & { code?: string }, tlsSocket: Socket & { _parent?: Socket }) => {
        if (err?.code === 'ERR_SSL_HTTP_REQUEST') {
          const raw = tlsSocket._parent ?? tlsSocket;
          if (raw && !raw.destroyed) {
            raw.end(
              'HTTP/1.1 302 Found\r\nLocation: https://localhost:5173/\r\nConnection: close\r\nContent-Length: 0\r\n\r\n',
            );
          }
        }
      },
    );
  },
});

const spaProxy = {
  target: BACKEND_URL,
  changeOrigin: false,
  bypass(req: { headers: { accept?: string } }) {
    if (req.headers.accept?.includes('text/html')) {
      return '/index.html';
    }
  },
};

export default defineConfig({
  plugins: [react(), tailwindcss(), httpToHttpsRedirect()],
  server: {
    host: true,
    port: 5173,
    strictPort: true,
    https: httpsConfig,
    proxy: {
      '/docs': { target: BACKEND_URL, changeOrigin: false },
      '/redoc': { target: BACKEND_URL, changeOrigin: false },
      '/openapi.json': { target: BACKEND_URL, changeOrigin: false },
      '/auth': spaProxy,
      '/current_user': { target: BACKEND_URL, changeOrigin: false },
      '/tenants': { target: BACKEND_URL, changeOrigin: false },
      '/tenant': { target: BACKEND_URL, changeOrigin: false },
      '/departments': spaProxy,
      '/members': spaProxy,
      '/invitations': { target: BACKEND_URL, changeOrigin: false },
      '/connections': { target: BACKEND_URL, changeOrigin: false },
      '/connectors': spaProxy,
      '/settings': spaProxy,
      '/health': { target: BACKEND_URL, changeOrigin: false },
      '/workflows': spaProxy,
      '/workflow-templates': { target: BACKEND_URL, changeOrigin: false },
      '/runs': { target: BACKEND_URL, changeOrigin: false },
      '/reviews': spaProxy,
      '/knowledge': spaProxy,
      '/admin': spaProxy,
    },
  },
  test: { environment: 'node' },
});
