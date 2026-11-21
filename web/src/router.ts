// The browser keeps the query (including ?sim=PORT) and History state for each app route. The
// Pages build emits a directory index for each route so a reload reaches the same screen.
import { PAGES } from './routes.ts';
import { routePath, SITE_BASE, siteHref } from './site.ts';

export type How = 'load' | 'push' | 'pop';
type Listener = (path: string, how: How) => void;
let listener: Listener = () => {};

export const currentPath = (): string => routePath(location.pathname) ?? '/';
export const backDepth = (): number => (history.state as { back?: number } | null)?.back ?? 0;

function routeHref(path: string): string {
  return siteHref(SITE_BASE === '/' || path.endsWith('/') ? path : path + '/');
}

/** Goes to an app-relative path and preserves the current query when no new query is given. */
export function navigate(path: string, options: { replace?: boolean; back?: number } = {}): void {
  const target = new URL(path, location.origin);
  const query = path.includes('?') ? target.search : location.search;
  const href = routeHref(target.pathname) + query + target.hash;
  const state = { back: options.back ?? 0 };
  if (options.replace) history.replaceState(state, '', href); else history.pushState(state, '', href);
  listener(currentPath(), 'push');
}

/** Routes the current URL, history navigation, and unmodified clicks on links to our pages. */
export function startRouter(onRoute: Listener): void {
  listener = onRoute;
  addEventListener('popstate', () => listener(currentPath(), 'pop'));
  document.addEventListener('click', (e) => {
    const a = (e.target as Element | null)?.closest?.('a[href]');
    if (!(a instanceof HTMLAnchorElement) || a.target || a.hasAttribute('download') || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const url = new URL(a.href);
    const path = url.origin === location.origin ? routePath(url.pathname) : null;
    if (!path || !PAGES.some((page) => path.split('/')[1] === page)) return;
    e.preventDefault();
    if (path !== currentPath() || url.search || url.hash) navigate(path + url.search + url.hash);
  });
  const old = PAGES.find((page) => '#' + page === location.hash);  // a page link from before the router
  if (old) history.replaceState({ back: 0 }, '', routeHref('/' + old) + location.search);
  listener(currentPath(), 'load');
}
