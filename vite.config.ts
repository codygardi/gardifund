import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

function dashboardOrigin(value: string): string {
  if (!value) return '';
  if (!/^https:\/\/[a-z0-9-]+(?:\.[a-z0-9-]+)+\.workers\.dev\/?$/i.test(value)) {
    throw new Error('GARDIFUND_DASHBOARD_URL must be the HTTPS root of a workers.dev hostname.');
  }
  return new URL(value).origin;
}

export default defineConfig(({ mode }) => {
  const pages = mode === 'pages';
  const origin = pages ? dashboardOrigin(process.env.GARDIFUND_DASHBOARD_URL ?? '') : '';
  return {
    base: pages ? './' : '/',
    envDir: false,
    define: { 'import.meta.env.GARDIFUND_VIEW_ORIGIN': JSON.stringify(origin) },
    publicDir: false,
    plugins: [react(), ...(pages ? [{
      name: 'gardifund-pages',
      transformIndexHtml: () => [{
        tag: 'meta', injectTo: 'head-prepend' as const,
        attrs: { 'http-equiv': 'Content-Security-Policy', content:
          `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src ${origin || "'none'"}; object-src 'none'; base-uri 'self'; form-action 'none'` },
      }],
      generateBundle(this: { emitFile(file: { type: 'asset'; fileName: string; source: string }): void }) {
        this.emitFile({ type: 'asset', fileName: '.nojekyll', source: '' });
      },
    }] : [])],
    server: { host: '127.0.0.1', port: 5173, proxy: { '/api': 'http://127.0.0.1:8765', '/scope': 'http://127.0.0.1:8765' } },
    build: { outDir: pages ? 'pages-dist' : 'dist', sourcemap: false },
  };
});
