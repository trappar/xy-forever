// npm run dev: the app on http://127.0.0.1:5173/. The client library is used from its TypeScript
// source (it has no built dist/ yet); xyfRelay lets ?sim=PORT reach the simulator over a WebSocket. appType 'spa':
// every path without a file serves index.html in dev and preview (the router's paths, src/router.ts).
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';
import type { Plugin } from 'vite';
import { INSTALL_STAGES, PAGES, PAGES_BASE } from './src/routes.ts';
import { xyfRelay } from './node_modules/xy-forever-client/test/relay.ts';

const client = (file: string): string => fileURLToPath(new URL(`./node_modules/xy-forever-client/src/${file}`, import.meta.url));

export const alias = [
  { find: /^xy-forever-client$/, replacement: client('index.ts') },
  { find: /^xy-forever-client\/dev$/, replacement: client('dev.ts') },
];

// Emit directory indexes after Vite rewrites the HTML with built asset URLs.
const routeIndexes: Plugin = {
  name: 'xyf-route-indexes',
  apply: 'build',
  generateBundle: {
    order: 'post',
    handler(_options, bundle) {
      const index = bundle['index.html'];
      if (index?.type !== 'asset') throw new Error('The web build did not produce index.html.');
      const paths = [...PAGES, ...INSTALL_STAGES.map((stage) => `install/${stage}`)];
      for (const path of paths) this.emitFile({ type: 'asset', fileName: `${path}/index.html`, source: index.source });
    },
  },
};

export default defineConfig(({ mode }) => ({
  appType: 'spa',
  base: mode === 'pages' ? PAGES_BASE : '/',
  server: { host: '127.0.0.1' },
  resolve: { alias, preserveSymlinks: false },
  plugins: [xyfRelay(), routeIndexes],
}));
