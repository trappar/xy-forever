// The app's routes (TanStack Router), in two sections (#316): Install (/install/$stage; / opens it) and Manage (/manage/
// the OP-XY itself, /manage/mods/, /manage/files/), always with a trailing slash so that each is a directory index on
// static hosting. The links of the earlier pages (/connect/, /device/, /mods/, /files/) lead to their Manage pages.
// The query (?sim=PORT among it) is kept across navigation. Components load lazily: vite.config.ts reads the paths it
// emits from this router in Node (staticPaths).
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
    if (useConnection.getState().uninstalling && !location.pathname.endsWith('/manage/'))
      throw redirect({ to: '/manage/' });
  },
  component: lazyRouteComponent(() => import('./shell.tsx'), 'AppShell'),
});

const home = createRoute({
  getParentRoute: () => root,
  path: '/',
  beforeLoad: () => {
    throw redirect({ to: '/install/', replace: true });
  },
});
/** Manage: its pages need a connection; without one it shows Connect in their place (pages/manage.tsx). */
const manage = createRoute({
  getParentRoute: () => root,
  path: 'manage',
  component: lazyRouteComponent(() => import('./pages/manage.tsx')),
});
const device = createRoute({
  getParentRoute: () => manage,
  path: '/',
  component: lazyRouteComponent(() => import('./pages/device.tsx')),
});
const mods = createRoute({
  getParentRoute: () => manage,
  path: 'mods',
  component: lazyRouteComponent(() => import('./pages/mods.tsx')),
});
const files = createRoute({
  getParentRoute: () => manage,
  path: 'files',
  component: lazyRouteComponent(() => import('./pages/files.tsx')),
});
/** The earlier pages' links (bookmarks, the README of a release) lead to their Manage pages. */
const moved = (path: string, to: '/manage/' | '/manage/mods/' | '/manage/files/') =>
  createRoute({
    getParentRoute: () => root,
    path,
    beforeLoad: () => {
      throw redirect({ to, replace: true });
    },
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
  install.addChildren([installIndex, installStage]),
  manage.addChildren([device, mods, files]),
  moved('connect', '/manage/'),
  moved('device', '/manage/'),
  moved('mods', '/manage/mods/'),
  moved('files', '/manage/files/'),
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
  const paths = Object.keys(router.routesByPath).flatMap((full) => {
    const path = full.replace(/^\/|\/$/g, '');
    return path.includes('$stage') ? INSTALL_STAGES.map((stage) => path.replace('$stage', stage)) : [path];
  });
  return [...new Set(paths)].filter((path) => path !== '');
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

/** Goes to an app path ('/manage/mods', '/install/<stage>'); the query is kept. */
export function navigatePath(path: string, options: { replace?: boolean; back?: number } = {}): void {
  const stage = path.match(/^\/install\/([^/]+)/)?.[1];
  const common = { replace: options.replace ?? false, state: (s: object) => ({ ...s, back: options.back ?? 0 }) };
  if (stage !== undefined && isInstallStage(stage))
    void router.navigate({ to: '/install/$stage/', params: { stage }, ...common });
  else
    void router.navigate({
      to: (path.endsWith('/') ? path : path + '/') as '/install/' | '/manage/' | '/manage/mods/' | '/manage/files/',
      ...common,
    });
}
