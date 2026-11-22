// The XY Forever web app. ?sim=PORT: the device simulator on 127.0.0.1:PORT through the dev server's relay.
import { StrictMode, useEffect } from 'react';
import { createRoot } from 'react-dom/client';
import './index.css';
import { Shell } from './shell.tsx';
import { PAGES } from './routes.ts';
import { startLegacy } from './legacy.ts';

function App() {
  useEffect(startLegacy, []);
  return (
    <Shell>
      {PAGES.map((page) => <section key={page} id={`page-${page}`} className="page" hidden />)}
    </Shell>
  );
}

createRoot(document.getElementById('root')!).render(<StrictMode><App /></StrictMode>);
