// Mods: packages on the device grouped by kind, with their saved on/off state; add packages from a generation folder
// (modkit split); remove a package or a whole set; the mods the OP-XY runs and Mod memory, saved as its Mods menu does
// (config.set: checked and kept, or refused with the reason; built the next time XY Forever starts).
import { useState } from 'react';
import type { ChangeEvent } from 'react';
import { useQuery } from '@tanstack/react-query';
import { configGet, configSet, fileListSource, isXyfError, listPackages, localSet, pushPackages, removePackage } from 'xy-forever-client';
import type { HeldGeneration, LocalSet, PackageSource, SavedInputs } from 'xy-forever-client';
import { Badge } from '@/components/ui/badge.tsx';
import { Button } from '@/components/ui/button.tsx';
import { Card, CardDescription, CardHeader, CardTitle } from '@/components/ui/card.tsx';
import { Checkbox } from '@/components/ui/checkbox.tsx';
import { Progress } from '@/components/ui/progress.tsx';
import { Table, TableBody, TableCell, TableRow } from '@/components/ui/table.tsx';
import { PageHeader } from '@/components/page-header.tsx';
import { confirmAction } from '../ask.tsx';
import { device, queryClient, savedConfig, useAction } from '../device.ts';
import { groupMods, human } from '../model.ts';
import type { SavedConfig } from '../model.ts';
import { notice } from '../notice.ts';

async function loadMods() {
  const c = device();
  const held = await listPackages(c);
  const { config, error } = await savedConfig(c);
  const inputs = error ? null : await configGet(c).catch((e: unknown) => { if (isXyfError(e, 'update_required', 'unsupported')) return null; throw e; });
  return { held, config, error, inputs };
}
const refresh = (): Promise<void> => queryClient.invalidateQueries({ queryKey: ['mods'] });
interface Removal { what: string; generation: string | null; label: string }

export default function ModsPage() {
  const { data, dataUpdatedAt } = useQuery({ queryKey: ['mods'], queryFn: loadMods, meta: { action: 'list_packages' } });
  const remove = useAction('remove_packages', async ({ what, generation, label }: Removal) => {
    const opts = generation ? { generation } : {};
    const plan = await removePackage(device(), what, { ...opts, dryRun: true });
    const files = plan.reduce((n, r) => n + r.files, 0);
    if (!await confirmAction(`Remove ${label}?`, `${files} files (${human(plan.reduce((n, r) => n + r.bytes, 0))}) are deleted from the OP-XY. A saved build that uses it keeps working until you change mods.`, 'Remove', true)) return;
    await removePackage(device(), what, opts);
    await refresh();
  });
  return (
    <section>
      <PageHeader title="Mods">Mod packages stored on the OP-XY, and the mods it runs.</PageHeader>
      {data && <>
        {data.error !== undefined && <p className="mb-4 text-muted-foreground">Could not read the saved configuration ({data.error}).</p>}
        <AddPackages />
        {data.held.length === 0
          ? <Card className="text-muted-foreground">No mod packages on this OP-XY yet.</Card>
          : data.held.map((g) => <GenerationCard key={g.generation} g={g} config={data.config} remove={(r) => remove.mutate(r)} />)}
        <InputsCard key={dataUpdatedAt} saved={data.inputs} held={data.held} />
      </>}
    </section>
  );
}

function chosenText(set: LocalSet): string {
  const files = [...set.packages.values()].flat();
  return `Chosen: ${set.profile} ${set.generation}, ${set.packages.size} packages, ${human(files.reduce((n, f) => n + f.data.length, 0))}.`;
}

function AddPackages() {
  const [chosen, setChosen] = useState<{ source: PackageSource; set: LocalSet } | null>(null);
  const [progress, setProgress] = useState<number | null>(null);
  const choose = useAction('choose_packages', async (files: File[]) => {
    setChosen(null);
    const source = fileListSource(files);
    setChosen({ source, set: await localSet(source) });
  });
  const send = useAction('send_packages', async () => {
    if (!chosen) throw new Error('Choose a package folder first.');
    setProgress(0);
    const summary = await pushPackages(device(), chosen.source, { onProgress: (done, total) => setProgress(total ? done / total : 0) })
      .finally(() => setProgress(null));
    setChosen(null);
    await refresh();
    return summary;
  }, 'Sending packages...');
  const pick = (event: ChangeEvent<HTMLInputElement>): void => {
    const files = [...event.target.files ?? []];
    event.target.value = '';
    choose.mutate(files);
  };
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex-1">Add packages</CardTitle>
        <Button asChild><label>Choose package folder...<input type="file" multiple hidden onChange={pick} {...{ webkitdirectory: '' }} /></label></Button>
        <Button variant="default" onClick={() => send.mutate(undefined, {
          onSuccess: (s) => notice(`Sent ${s.sent} package${s.sent === 1 ? '' : 's'} (${human(s.bytes)}); ${s.skipped} already there.`),
        })}>Send to OP-XY</Button>
      </CardHeader>
      <CardDescription id="mods-chosen">{chosen ? chosenText(chosen.set) : 'Choose a generation folder (from modkit split): it holds base/ and one folder per mod.'}</CardDescription>
      {progress !== null && <Progress value={progress * 100} />}
    </Card>
  );
}

function GenerationCard({ g, config, remove }: { g: HeldGeneration; config: SavedConfig | null | undefined; remove: (r: Removal) => void }) {
  if (g.older || g.newer) {                          // no store.txt, or a newer store version (#124)
    const kind = g.older ? 'older' : 'newer';
    return (
      <Card>
        <CardHeader>
          <CardTitle className="flex-1">{g.generation} ({kind} layout)</CardTitle>
          <Button size="sm" variant="danger" onClick={() => remove({ what: g.generation, generation: null, label: `the ${kind} ${g.generation} folder` })}>Remove all</Button>
        </CardHeader>
        <CardDescription>{g.older
          ? 'Packages sent by an older XY Forever, in folders too deep for the dev menu\'s file transfer. Remove them, then send the package set again.'
          : 'Packages in a newer layout than this page reads: update it, or remove them and send the package set again.'}</CardDescription>
      </Card>
    );
  }
  const sameSet = config && config.profile === g.profile && config.generation === g.generation;
  const groups = groupMods(g.packages, sameSet || config === undefined ? config : null);
  return (
    <Card>
      <CardHeader>
        <CardTitle>{g.profile} {g.generation}</CardTitle>
        {!g.packages.some((p) => p.name === 'base') && <Badge variant="warn">no base package</Badge>}
        <span className="flex-1" />
        <Button size="sm" variant="danger" onClick={() => remove({ what: g.generation, generation: null, label: `the whole ${g.generation} package set` })}>Remove all</Button>
      </CardHeader>
      <CardDescription>{config === undefined ? 'On/off: the saved configuration could not be read.' : sameSet ? 'On/off: as saved on the device.' : 'On/off: this set is not the saved one, so all are off.'}</CardDescription>
      <Table>
        <TableBody>
          {groups.flatMap((group) => [
            <TableRow key={group.kind}><TableCell colSpan={4} className="pt-3.5 text-xs tracking-widest text-primary uppercase">{group.label}</TableCell></TableRow>,
            ...group.rows.map((r) => (
              <TableRow key={r.folder} data-mod={r.folder}>
                <TableCell><b>{r.name}</b> <span className="font-mono text-[13px] text-muted-foreground">{r.version}</span></TableCell>
                <TableCell className="space-x-1">
                  {r.on === null ? <Badge>unknown</Badge> : <Badge variant={r.on ? 'ok' : 'default'}>{r.on ? 'on' : 'off'}</Badge>}
                  {!r.complete && <Badge variant="warn" title="Sending was interrupted: send it again.">incomplete</Badge>}
                </TableCell>
                <TableCell className="text-muted-foreground max-md:hidden">{human(r.bytes)}</TableCell>
                <TableCell className="text-right"><Button size="sm" variant="danger" onClick={() => remove({ what: r.folder, generation: g.generation, label: r.name })}>Remove</Button></TableCell>
              </TableRow>
            )),
          ])}
        </TableBody>
      </Table>
    </Card>
  );
}

/** The mods the OP-XY runs (#26): saved as turning them on in the device's Mods menu does (config.set). The device checks
 * that it can build them and keeps them, or says why not; it builds them the next time XY Forever starts. */
function InputsCard({ saved, held }: { saved: SavedInputs | null; held: HeldGeneration[] }) {
  const gens = held.filter((g) => g.packages.some((p) => p.name === 'base' && p.complete));
  const g = gens.find((x) => x.generation === saved?.generation) ?? gens[0];
  const rows = g ? groupMods(g.packages.filter((p) => p.complete), null).flatMap((grp) => grp.rows) : [];
  const [on, setOn] = useState(() => new Set(saved?.mods ?? []));
  const [expanded, setExpanded] = useState(saved?.expanded ?? false);
  const [unverified, setUnverified] = useState(saved?.unverified ?? false);
  const [status, setStatus] = useState(saved?.write === 'failed' ? 'The last change could not be written: save again.' : '');
  const save = useAction('save_mods', async () => {
    try {
      await configSet(device(), { mods: rows.filter((r) => on.has(r.folder)).map((r) => r.folder), expanded, unverified });
      setStatus('Saved. The OP-XY builds and installs it the next time it starts.');
    } catch (error) {
      if (!isXyfError(error, 'refused')) throw error;
      setStatus(`Not saved: ${error.message}`);
    }
  }, 'Saving on the OP-XY...');
  const toggle = (folder: string, checked: boolean): void => setOn((now) => {
    const next = new Set(now);
    if (checked) next.add(folder); else next.delete(folder);
    return next;
  });
  const body = !saved ? <p>This OP-XY's XY Forever cannot set mods over USB (it needs protocol 1.24): update it first.</p>
    : !g ? <p>Send a package set with its base first.</p>
    : <>
      <div className="grid grid-cols-[repeat(auto-fill,minmax(180px,1fr))] gap-x-3.5 gap-y-1">
        {rows.map((r) => <Choice key={r.folder} checked={on.has(r.folder)} onChange={(c) => toggle(r.folder, c)}>{r.name}<span className="text-muted-foreground">{r.kind}</span></Choice>)}
      </div>
      <div className="flex flex-wrap gap-4">
        <Choice checked={expanded} onChange={setExpanded}>4 MiB mod memory</Choice>
        <Choice checked={unverified} onChange={setUnverified}>Allow an unknown base</Choice>
      </div>
      <div><Button variant="default" onClick={() => save.mutate()}>Save</Button></div>
      <p className="text-muted-foreground" id="inputs-status">{status}</p>
    </>;
  return (
    <Card id="inputs">
      <CardTitle>Mods on the OP-XY</CardTitle>
      <CardDescription>Saving works like turning mods on in the device's Mods menu: the OP-XY checks that it can build them and keeps them. It builds them the next time it starts.</CardDescription>
      {body}
    </Card>
  );
}

const Choice = ({ checked, onChange, children }: { checked: boolean; onChange: (checked: boolean) => void; children: React.ReactNode }) =>
  <label className="flex items-center gap-2"><Checkbox checked={checked} onCheckedChange={(c) => onChange(c === true)} />{children}</label>;
