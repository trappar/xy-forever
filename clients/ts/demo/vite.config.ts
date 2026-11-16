// npm run dev: the demo page on http://127.0.0.1:5173/, with the simulator relay (test/relay.ts).
import { defineConfig } from 'vite';
import { xyfRelay } from '../test/relay.ts';

export default defineConfig({
  server: { host: '127.0.0.1' },
  plugins: [xyfRelay()],
});
