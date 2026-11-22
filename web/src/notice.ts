// The notice line as one sonner toast: an error stays until replaced or closed, a busy one until the work ends.
import { toast } from 'sonner';

export type NoticeKind = 'error' | 'busy' | 'info';

export function notice(text: string, kind: NoticeKind = 'info'): void {
  if (!text) { toast.dismiss('notice'); return; }
  const options = { id: 'notice', testId: 'notice', duration: kind === 'info' ? 6000 : Infinity };
  if (kind === 'error') toast.error(text, options);
  else if (kind === 'busy') toast.loading(text, options);
  else toast(text, options);
}
