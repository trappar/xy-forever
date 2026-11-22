// The Install page, not moved to React yet, mounted in the router's outlet.
import { useEffect, useRef } from 'react';
import { useParams } from '@tanstack/react-router';
import { installSection, showInstall } from './legacy.ts';

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
