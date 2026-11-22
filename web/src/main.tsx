// The XY Forever web app: the router renders the shell and the pages (routing.ts); TanStack Query holds device data
// (device.ts).
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClientProvider } from '@tanstack/react-query';
import { RouterProvider } from '@tanstack/react-router';
import './index.css';
import { queryClient } from './device.ts';
import { router } from './routing.ts';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <QueryClientProvider client={queryClient}>
      <RouterProvider router={router} />
    </QueryClientProvider>
  </StrictMode>,
);
