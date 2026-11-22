// The app's questions: a confirm (AlertDialog) or one line of text (Dialog), asked from anywhere and answered once.
import { useState } from 'react';
import type { FormEvent } from 'react';
import { create } from 'zustand';
import { AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogFooter,
  AlertDialogHeader, AlertDialogTitle } from '@/components/ui/alert-dialog.tsx';
import { Button, buttonVariants } from '@/components/ui/button.tsx';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog.tsx';
import { Input } from '@/components/ui/input.tsx';

interface Question { title: string; body: string; ok: string; danger: boolean; value: string | null; answer: (value: string | null) => void }
const useQuestion = create<{ asked: Question | null }>(() => ({ asked: null }));

function ask(q: Omit<Question, 'answer'>): Promise<string | null> {
  useQuestion.getState().asked?.answer(null);
  return new Promise((resolve) => useQuestion.setState({ asked: { ...q, answer: (value) => { useQuestion.setState({ asked: null }); resolve(value); } } }));
}

/** Asks in plain words; resolves true only on the confirm button. */
export const confirmAction = (title: string, body: string, ok: string, danger = false): Promise<boolean> =>
  ask({ title, body, ok, danger, value: null }).then((v) => v !== null);

/** Asks for one line of text; null when cancelled. */
export const promptText = (title: string, body: string, value: string, ok = 'OK'): Promise<string | null> =>
  ask({ title, body, ok, danger: false, value });

export function AskDialog() {
  const asked = useQuestion((s) => s.asked);
  if (!asked) return null;
  const close = (open: boolean): void => { if (!open) asked.answer(null); };
  if (asked.value === null) return (
    <AlertDialog open onOpenChange={close}>
      <AlertDialogContent>
        <AlertDialogHeader><AlertDialogTitle>{asked.title}</AlertDialogTitle><AlertDialogDescription>{asked.body}</AlertDialogDescription></AlertDialogHeader>
        <AlertDialogFooter>
          <AlertDialogCancel>Cancel</AlertDialogCancel>
          <AlertDialogAction className={asked.danger ? buttonVariants({ variant: 'destructive' }) : ''} onClick={() => asked.answer('')}>{asked.ok}</AlertDialogAction>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
  return <PromptDialog asked={asked} close={close} />;
}

function PromptDialog({ asked, close }: { asked: Question; close: (open: boolean) => void }) {
  const [value, setValue] = useState(asked.value ?? '');
  const submit = (e: FormEvent): void => { e.preventDefault(); asked.answer(value); };
  return (
    <Dialog open onOpenChange={close}>
      <DialogContent>
        <form onSubmit={submit} className="grid gap-4">
          <DialogHeader><DialogTitle>{asked.title}</DialogTitle><DialogDescription>{asked.body}</DialogDescription></DialogHeader>
          <Input aria-label={asked.title} value={value} onChange={(e) => setValue(e.target.value)} autoFocus />
          <DialogFooter>
            <Button onClick={() => close(false)}>Cancel</Button>
            <Button type="submit" variant="default">{asked.ok}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
