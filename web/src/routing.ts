// The app's routes (TanStack Router): /connect (also /), /install/$stage, /device, /mods, /files, under Vite's base
// (/ in development, /xy-forever/ for GitHub Pages), always with a trailing slash so that each is a directory index on
// static hosting. The query (?sim=PORT among it) is kept across navigation. Components load lazily: vite.config.ts
// reads the paths it emits from this router in Node (staticPaths).
import {
  createRootRoute,
  createRoute,
  createRouter,
  lazyRouteComponent,
  redirect,
  retainSearchParams,
} from '@tanstack/react-router';
import { useConnection } from './connection.ts';
import { INSTALL_STAGES, isInstallStage } from './install/stages.ts';
import type { InstallStage } from './install/stages.ts';

export type Page = 'connect' | 'install' | 'device' | 'mods' | 'files';
/** ?sim=PORT: the device simulator on 127.0.0.1:PORT through the dev server's relay (development and tests). */
export interface AppSearch {
  sim?: string;
  [key: string]: unknown;
}

const root = createRootRoute({
  validateSearch: (search: Record<string, unknown>): AppSearch => {
    const { sim, ...rest } = search;
    return sim === undefined || sim === null || sim === '' ? rest : { ...rest, sim: String(sim) };
  },
  search: { middlewares: [retainSearchParams(true)] },
  beforeLoad: ({ location }) => {
    // an uninstall in progress keeps the Device page
    if (useConnection.getState().uninstalling && !location.pathname.endsWith('/device/'))
      throw redirect({ to: '/device/' });
  },
  component: lazyRouteComponent(() => import('./shell.tsx'), 'AppShell'),
});

/** Device, Mods and Files need a connection: without one, Connect. */
const connected = (): void => {
  if (!useConnection.getState().client) throw redirect({ to: '/connect/', replace: true });
};

const home = createRoute({
  getParentRoute: () => root,
  path: '/',
  beforeLoad: () => {
    throw redirect({ to: '/connect/', replace: true });
  },
});
const connect = createRoute({
  getParentRoute: () => root,
  path: 'connect',
  component: lazyRouteComponent(() => import('./pages/connect.tsx')),
});
const device = createRoute({
  getParentRoute: () => root,
  path: 'device',
  beforeLoad: connected,
  component: lazyRouteComponent(() => import('./pages/device.tsx')),
});
const mods = createRoute({
  getParentRoute: () => root,
  path: 'mods',
  beforeLoad: connected,
  component: lazyRouteComponent(() => import('./pages/mods.tsx')),
});
const files = createRoute({
  getParentRoute: () => root,
  path: 'files',
  beforeLoad: connected,
  component: lazyRouteComponent(() => import('./pages/files.tsx')),
});
const install = createRoute({ getParentRoute: () => root, path: 'install' });
const installIndex = createRoute({
  getParentRoute: () => install,
  path: '/',
  component: lazyRouteComponent(() => import('./pages/install.tsx')),
});
const installStage = createRoute({
  getParentRoute: () => install,
  path: '$stage',
  params: {
    parse: ({ stage }): { stage: InstallStage } => {
      if (!isInstallStage(stage)) throw redirect({ to: '/install/' });
      return { stage };
    },
    stringify: ({ stage }) => ({ stage }),
  },
  component: lazyRouteComponent(() => import('./pages/install.tsx')),
});

const routeTree = root.addChildren([
  home,
  connect,
  device,
  mods,
  files,
  install.addChildren([installIndex, installStage]),
]);

export const router = createRouter({
  routeTree,
  basepath: import.meta.env?.BASE_URL ?? '/', // no import.meta.env when Node reads staticPaths
  trailingSlash: 'always',
  defaultPreload: false,
});

declare module '@tanstack/react-router' {
  interface Register {
    router: typeof router;
  }
  interface HistoryState {
    back?: number;
  }
}

/** The paths the static build emits an index.html for: every route, each install stage. */
export function staticPaths(): string[] {
  return Object.keys(router.routesByPath)
    .flatMap((full) => {
      const path = full.replace(/^\/|\/$/g, '');
      return path.includes('$stage') ? INSTALL_STAGES.map((stage) => path.replace('$stage', stage)) : [path];
    })
    .filter((path) => path !== '');
}

/** The simulator's port from ?sim, or null for Web Serial. */
export function simPort(): string | null {
  const { sim } = router.state.location.search;
  return typeof sim === 'string' ? sim : null;
}

/** The path this document was loaded at. */
export const loadPath = typeof location === 'undefined' ? '' : location.pathname;

/** How many stages lie behind this one in the install flow (the history entry's state). */
export const backDepth = (): number => router.state.location.state.back ?? 0;

/** Goes to an app path ('/device', '/install/<stage>'); the query is kept. */
export function navigatePath(path: string, options: { replace?: boolean; back?: number } = {}): void {
  const stage = path.match(/^\/install\/([^/]+)/)?.[1];
  const common = { replace: options.replace ?? false, state: (s: object) => ({ ...s, back: options.back ?? 0 }) };
  if (stage !== undefined && isInstallStage(stage))
    void router.navigate({ to: '/install/$stage/', params: { stage }, ...common });
  else
    void router.navigate({
      to: (path.endsWith('/') ? path : path + '/') as '/connect/' | '/device/' | '/mods/' | '/files/' | '/install/',
      ...common,
    });
}
