// The web app's router (#178): one path per page and per install stage (History API), so a reload keeps the place and
// back and forward work. Every path serves index.html: Vite's SPA fallback in dev and preview; hosting needs the same.
// The query (?sim=PORT) is kept across paths. history.state.back counts the install stages behind this one in the
// flow (the Install page's own Back button); 0 elsewhere.

export type How = 'load' | 'push' | 'pop';
type Listener = (path: string, how: How) => void;
let listener: Listener = () => {};

/** Install stages behind the current entry in this flow. */
export const backDepth = (): number => (history.state as { back?: number } | null)?.back ?? 0;

/** Goes to path (a new history entry, or in place with replace), then routes. */
export function navigate(path: string, options: { replace?: boolean; back?: number } = {}): void {
  const url = path.includes('?') ? path : `${path}${location.search}`;
  const state = { back: options.back ?? 0 };
  if (options.replace) history.replaceState(state, '', url); else history.pushState(state, '', url);
  listener(location.pathname, 'push');
}

/** Routes the current path now, on back and forward, and on clicks of in-app links (href starting with /). */
export function startRouter(onRoute: Listener): void {
  listener = onRoute;
  addEventListener('popstate', () => listener(location.pathname, 'pop'));
  document.addEventListener('click', (e) => {
    const a = (e.target as Element | null)?.closest?.('a');
    const href = a?.getAttribute('href');
    if (!a || !href?.startsWith('/') || a.target || a.hasAttribute('download') || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    if (href !== location.pathname) navigate(href);
  });
  const old = location.hash.slice(1);                       // a #page link from before the router
  if (/^[a-z]+$/.test(old)) history.replaceState({ back: 0 }, '', `/${old}${location.search}`);
  listener(location.pathname, 'load');
}
