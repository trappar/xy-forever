// The pages not moved to React yet, mounted in the router's outlet: each renders its DOM into its section.
import { useEffect, useRef } from 'react';
import { useParams } from '@tanstack/react-router';
import { installSection, showInstall, showPage } from './legacy.ts';
import type { Page } from './routing.ts';

export function LegacyPage({ page }: { page: Exclude<Page, 'install'> }) {
  const ref = useRef<HTMLElement>(null);
  const shown = useRef(false);
  useEffect(() => {
    if (shown.current) return;          // StrictMode runs effects twice: load the page once
    shown.current = true;
    showPage(page);
  }, [page]);
  return <section ref={ref} id={`page-${page}`} className="page" />;
}

/** The install flow keeps one section across its stages (it holds the flow's state). */
export function LegacyInstall() {
  const { stage } = useParams({ strict: false });
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const section = installSection();
    ref.current!.append(section);
    section.hidden = false;
    return () => { section.hidden = true; section.remove(); };
  }, []);
  useEffect(() => { showInstall(stage); }, [stage]);
  return <div ref={ref} />;
}
