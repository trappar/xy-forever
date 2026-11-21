// Vite supplies a trailing slash in both the development and project-site bases.
export const SITE_BASE = import.meta.env.BASE_URL;
export const siteHref = (path: string): string => SITE_BASE + path.replace(/^\//, '');

export function routePath(pathname: string): string | null {
  if (pathname + '/' === SITE_BASE) return '/';
  if (!pathname.startsWith(SITE_BASE)) return null;
  const local = '/' + pathname.slice(SITE_BASE.length);
  return local.replace(/\/(?:index\.html)?$/, '') || '/';
}
