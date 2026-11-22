// npm run dev: the app on http://127.0.0.1:5173/. The client library is used from its TypeScript
// source (it has no built dist/ yet); xyfRelay lets ?sim=PORT reach the simulator over a WebSocket. appType 'spa':
// every path without a file serves index.html in dev and preview (the router's paths, src/routing.ts).
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import tailwindcss from '@tailwindcss/vite';
import type { Plugin } from 'vite';
import { xyfRelay } from './node_modules/xy-forever-client/test/relay.ts';

const client = (file: string): string => fileURLToPath(new URL(`./node_modules/xy-forever-client/src/${file}`, import.meta.url));

/** The GitHub Pages project path (npm run build:pages). */
export const PAGES_BASE = '/xy-forever/';

export const alias = [
  { find: /^@\//, replacement: fileURLToPath(new URL('./src/', import.meta.url)) },
  { find: /^xy-forever-client$/, replacement: client('index.ts') },
  { find: /^xy-forever-client\/dev$/, replacement: client('dev.ts') },
];

// Copy the rewritten HTML for static routes and keep the notices with the published files.
const staticSite: Plugin = {
  name: 'xyf-static-site',
  apply: 'build',
  generateBundle: {
    order: 'post',
    async handler(_options, bundle) {
      const index = bundle['index.html'];
      if (index?.type !== 'asset') throw new Error('The web build did not produce index.html.');
      // The router's paths, read at build time by Node itself (the config bundle would follow its lazy pages).
      const routing = new URL('./src/routing.ts', import.meta.url).href;
      const { staticPaths } = await import(/* @vite-ignore */ routing) as typeof import('./src/routing.ts');
      for (const path of staticPaths()) this.emitFile({ type: 'asset', fileName: `${path}/index.html`, source: index.source });
      const notices = ['../LICENSE', 'src/vendor/open-xy/LICENSE', 'src/vendor/open-xy/ferret.LICENSE',
        '../third_party/tiny-json/LICENSE'].map((path) => readFileSync(new URL(path, import.meta.url), 'utf8').trim());
      this.emitFile({ type: 'asset', fileName: 'LICENSES.txt', source: notices.join('\n\n') + '\n' });
    },
  },
};

export default defineConfig(({ mode }) => ({
  appType: 'spa',
  base: mode === 'pages' ? PAGES_BASE : '/',
  server: { host: '127.0.0.1' },
  resolve: { alias, preserveSymlinks: false },
  plugins: [react(), tailwindcss(), xyfRelay(), staticSite],
}));
