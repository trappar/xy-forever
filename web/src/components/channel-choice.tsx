// The firmware channel (#301): Stable, the pinned release (the default), or Canary, the verified build of the latest
// firmware change on main, experimental. Shown by Install and Update only when the site offers a canary.
import { useEffect, useState } from 'react';
import { Badge } from '@/components/ui/badge.tsx';
import { channelOf, fetchCanary } from '../install/release.ts';
import type { Channel, ReleasePin } from '../install/release.ts';

let canaryOnce: Promise<ReleasePin | null> | null = null;
/** The site's canary (read once per page load), or null: none, or not read yet. */
export function useCanary(): ReleasePin | null {
  const [canary, setCanary] = useState<ReleasePin | null>(null);
  useEffect(() => {
    let shown = true;
    void (canaryOnce ??= fetchCanary()).then((found) => shown && setCanary(found));
    return () => {
      shown = false;
    };
  }, []);
  return canary;
}

/** A release's build identity: what the page will copy or install. */
function releaseFacts(release: ReleasePin): string {
  const facts = `XY Forever ${release.version}, image ${release.image}`;
  return channelOf(release) === 'canary'
    ? `${facts}, built ${release.date!.slice(0, 10)} from commit ${release.commit!.slice(0, 7)}`
    : facts;
}

/** The release about to be copied or installed: its name, channel and build identity. */
export const ReleaseIdentity = ({ release }: { release: ReleasePin }) => (
  <p data-testid="release-identity" className="text-muted-foreground">
    {channelOf(release) === 'canary' ? 'Canary (experimental)' : 'Stable'}: {releaseFacts(release)}.
  </p>
);

export function ChannelChoice({
  stable,
  canary,
  value,
  onChange,
  disabled = false,
}: {
  stable: ReleasePin;
  canary: ReleasePin;
  value: Channel;
  onChange: (channel: Channel) => void;
  disabled?: boolean;
}) {
  const option = (channel: Channel, release: ReleasePin) => (
    <label className="flex items-baseline gap-2">
      <input
        type="radio"
        name="channel"
        value={channel}
        checked={value === channel}
        onChange={() => onChange(channel)}
      />
      <span>
        {channel === 'stable' ? 'Stable' : 'Canary'}{' '}
        {channel === 'canary' && <Badge variant="warn">Experimental</Badge>}{' '}
        <span className="text-muted-foreground">{releaseFacts(release)}</span>
      </span>
    </label>
  );
  return (
    <fieldset data-testid="channel" disabled={disabled} className="flex flex-col gap-1.5">
      <legend className="mb-1 text-sm font-semibold">Firmware channel</legend>
      {option('stable', stable)}
      {option('canary', canary)}
      {value === 'canary' && (
        <p className="text-warn">
          Canary is a build of the latest change to XY Forever’s main branch. It has passed the automated checks but not
          a release review. Choose Stable for everyday use.
        </p>
      )}
    </fieldset>
  );
}
