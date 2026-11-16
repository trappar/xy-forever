// npm run dev: the app on http://127.0.0.1:5173/. The client library is used from its TypeScript
// source (it has no built dist/ yet); xyfRelay lets ?sim=PORT reach the simulator over a WebSocket.
import { fileURLToPath } from 'node:url';
import { defineConfig } from 'vite';
import { xyfRelay } from './node_modules/xy-forever-client/test/relay.ts';

const client = (file: string): string => fileURLToPath(new URL(`./node_modules/xy-forever-client/src/${file}`, import.meta.url));

export const alias = [
  { find: /^xy-forever-client$/, replacement: client('index.ts') },
  { find: /^xy-forever-client\/dev$/, replacement: client('dev.ts') },
];

export default defineConfig({
  server: { host: '127.0.0.1' },
  resolve: { alias, preserveSymlinks: false },
  plugins: [xyfRelay()],
});
