// Files: browse /yaffs2, download, upload, new folder, rename/move, copy, delete. Changes are allowed only in the write
// area (model.ts: the device refuses the rest too); removals ask first.
import { useState } from 'react';
import type { ChangeEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import type { DirEntry } from 'xy-forever-client';
import { Button } from '@/components/ui/button.tsx';
import { Card } from '@/components/ui/card.tsx';
import { Progress } from '@/components/ui/progress.tsx';
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table.tsx';
import { PageHeader } from '@/components/page-header.tsx';
import { downloadBlob } from '@/lib/download.ts';
import { confirmAction, promptText } from '../ask.tsx';
import { device, queryClient, useAction } from '../device.ts';
import { ROOT, WRITE_RULE, changeRefusal, folderWritable, human, joinPath, nameProblem, parentOf } from '../model.ts';

async function list(path: string): Promise<DirEntry[]> {
  const entries = await device().listDir(path);
  return entries.sort((a, b) => Number(a.kind !== 'folder') - Number(b.kind !== 'folder') || a.name.localeCompare(b.name));
}
const refresh = (): Promise<void> => queryClient.invalidateQueries({ queryKey: ['files'] });
const refuse = (why: string | null): void => { if (why) throw new Error(why); };

export default function FilesPage() {
  const [cwd, setCwd] = useState(ROOT);
  /** The transfer under way: a fraction, null without a known total, undefined for none. */
  const [progress, setProgress] = useState<number | null | undefined>(undefined);
  const { data: entries } = useQuery({ queryKey: ['files', cwd], queryFn: () => list(cwd), meta: { action: 'browse_files' } });
  const open = (path: string): void => setCwd(path.replace(/\/+$/, '') || ROOT);
  const writable = folderWritable(cwd);
  const parts = cwd.split('/').filter(Boolean);

  const download = useAction('download_file', async (e: DirEntry) => {
    setProgress(null);
    try { downloadBlob(new Blob([await device().readFile(joinPath(cwd, e.name)) as BlobPart]), e.name); } finally { setProgress(undefined); }
  }, 'Downloading...');
  const upload = useAction('upload_files', async (files: File[]) => {
    const existing = new Set((await device().listDir(cwd)).map((e) => e.name));
    const replaced = files.filter((f) => existing.has(f.name)).map((f) => f.name);
    if (replaced.length && !await confirmAction('Replace files?', `These are replaced on the OP-XY:\n${replaced.join('\n')}`, 'Replace', true)) return;
    try {
      for (const f of files) {
        const target = joinPath(cwd, f.name);
        refuse(changeRefusal(target));
        await device().writeFile(target, new Uint8Array(await f.arrayBuffer()), { onProgress: (done, total) => setProgress(total ? done / total : null) });
      }
    } finally { setProgress(undefined); }
    await refresh();
  }, 'Uploading...');
  const newFolder = useAction('new_folder', async () => {
    const name = await promptText('New folder', `In ${cwd}`, '', 'Create');
    if (name === null) return;
    refuse(nameProblem(name) ?? changeRefusal(joinPath(cwd, name), true));
    await device().makeDir(joinPath(cwd, name));
    await refresh();
  });
  const move = useAction('move_file', async (path: string) => {
    const target = await promptText('Rename or move', `Type the new full path for ${path}. Nothing is replaced.`, path, 'Rename');
    if (target === null || target === path) return;
    refuse(changeRefusal(target));
    await device().renamePath(path, target);
    await refresh();
  });
  const copy = useAction('copy_file', async (e: DirEntry) => {
    const path = joinPath(cwd, e.name);
    const suggestion = writable ? `${path}-copy` : joinPath('/yaffs2/content', e.name);
    const target = await promptText(`Copy ${e.kind}`, `Type the full path of the copy. It must be ${WRITE_RULE}.`, suggestion, 'Copy');
    if (target === null) return;
    refuse(changeRefusal(target));
    await device().copyTree(path, target);
    await refresh();
  });
  const remove = useAction('delete_file', async (e: DirEntry) => {
    const path = joinPath(cwd, e.name);
    if (e.kind === 'folder' && (await device().listDir(path)).length) throw new Error(`${e.name} is not empty: delete what it holds first.`);
    if (!await confirmAction(`Delete ${e.name}?`, `${path} is deleted from the OP-XY. This cannot be undone.`, 'Delete', true)) return;
    if (e.kind === 'folder') await device().removeDir(path); else await device().deleteFile(path);
    await refresh();
  });
  const pick = (event: ChangeEvent<HTMLInputElement>): void => {
    const files = [...event.target.files ?? []];
    event.target.value = '';
    if (files.length) upload.mutate(files);
  };

  return (
    <section>
      <PageHeader title="Files">Browse the OP-XY's storage. Everything can be downloaded.</PageHeader>
      <p className="mb-3 text-[13px] text-muted-foreground" data-testid="files-rules">
        Changes are allowed {WRITE_RULE}. {writable ? 'This folder accepts changes.' : 'This folder is read-only.'}
      </p>
      <Card>
        <div className="flex flex-wrap items-center gap-2.5">
          <nav aria-label="Folder" className="flex flex-1 flex-wrap items-center gap-1 font-mono text-[13px]">
            {parts.map((p, i) => <span key={i} className="contents">
              {i > 0 && '/'}
              {i === parts.length - 1 ? <b>{p}</b> : <Button size="sm" className="font-mono" onClick={() => open('/' + parts.slice(0, i + 1).join('/'))}>{p}</Button>}
            </span>)}
          </nav>
          {writable && <>
            <Button asChild><label>Upload...<input type="file" multiple hidden onChange={pick} /></label></Button>
            <Button onClick={() => newFolder.mutate()}>New folder</Button>
          </>}
        </div>
        {progress !== undefined && <Progress value={progress === null ? null : progress * 100} />}
        <Table>
          <TableHeader><TableRow><TableHead>Name</TableHead><TableHead className="max-md:hidden">Size</TableHead><TableHead /></TableRow></TableHeader>
          <TableBody id="files-body">
            {cwd !== ROOT && <TableRow><TableCell colSpan={3}><Button variant="link" onClick={() => open(parentOf(cwd))}>.. (up)</Button></TableCell></TableRow>}
            {entries?.map((e) => (
              <TableRow key={e.name} data-name={e.name} data-kind={e.kind} data-size={e.kind === 'file' ? e.size : undefined}>
                <TableCell>{e.kind === 'folder' ? <Button variant="link" onClick={() => open(joinPath(cwd, e.name))}>{e.name}/</Button> : e.name}</TableCell>
                <TableCell className="text-muted-foreground max-md:hidden">{e.kind === 'file' ? human(e.size) : ''}</TableCell>
                <TableCell className="space-x-1 text-right whitespace-nowrap">
                  {e.kind === 'file' && <Button size="sm" onClick={() => download.mutate(e)}>Download</Button>}
                  {e.kind !== 'other' && <Button size="sm" onClick={() => copy.mutate(e)}>Copy</Button>}
                  {writable && e.kind !== 'other' && <Button size="sm" onClick={() => move.mutate(joinPath(cwd, e.name))}>Rename/Move</Button>}
                  {writable && e.kind !== 'other' && <Button size="sm" variant="danger" onClick={() => remove.mutate(e)}>Delete</Button>}
                </TableCell>
              </TableRow>
            ))}
            {entries?.length === 0 && <TableRow><TableCell colSpan={3} className="text-muted-foreground">Empty folder.</TableCell></TableRow>}
          </TableBody>
        </Table>
      </Card>
    </section>
  );
}
