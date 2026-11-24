import { describe, expect, it } from 'vitest';
import type { HeldPackage } from 'xy-forever-client';
import {
  changeRefusal,
  configText,
  contentText,
  folderWritable,
  groupMods,
  joinPath,
  nameProblem,
  parentOf,
  parseConfig,
  parseKeyValues,
} from '../src/model.ts';

const pkg = (name: string, info: Record<string, string>, complete = true): HeldPackage => ({
  name,
  info,
  files: 3,
  bytes: 4096,
  complete,
});

describe('config parsing', () => {
  it('reads key=value lines with CRLF, comments and duplicates', () => {
    expect(parseKeyValues('# note\r\nbase = fw1\r\nmods=a\nmods=b,c\njunk\n=x\n')).toEqual({
      base: 'fw1',
      mods: 'b,c',
    });
  });
  it('parses the saved configuration', () => {
    const c = parseConfig(
      'base=stock-1\nprofile=opxy\ngeneration=1.1.0\nunverified=1\nslot=lib3\nmods=drift, , tape\n',
    );
    expect(c).toMatchObject({
      base: 'stock-1',
      profile: 'opxy',
      generation: '1.1.0',
      unverified: true,
      slot: 'lib3',
      mods: ['drift', 'tape'],
    });
    expect(configText(c)).toBe('2 mods on (drift, tape), built into library entry lib3.');
  });
  it('treats empty values as missing', () => {
    const c = parseConfig('base=\nmods=\n');
    expect(c.base).toBeNull();
    expect(configText(c)).toBe('all mods off, not built yet.');
    expect(configText(null)).toMatch(/No mods saved/);
  });
  it('describes the content state', () => {
    expect(contentText(null, false)).toBe('Official content.');
    expect(contentText(null, true)).toMatch(/Needs repair/);
    expect(contentText('state=installed\nentry=lib3\n', false)).toBe('Mod content installed for lib3.');
    expect(contentText('state=switching\ntarget=lib4', false)).toMatch(/lib4 did not finish/);
  });
});

describe('grouping', () => {
  const held = [
    pkg('base', { type: 'base' }),
    pkg('tape', { type: 'mod', machine: 'effect', id: 'tape', name: 'Tape' }),
    pkg('ext-seq', { type: 'mod', machine: 'extension' }),
    pkg('zeta', { type: 'mod', machine: 'synth', name: 'Zeta' }),
    pkg('grain', { type: 'mod', machine: 'sample', name: 'Grain' }),
    pkg('alpha', { type: 'mod', machine: 'synth', name: 'Alpha' }, false),
    pkg('odd', {}),
  ];
  it('groups by kind in menu order, then by name, without the base', () => {
    const groups = groupMods(held, parseConfig('mods=zeta,seq'));
    expect(groups.map((g) => g.label)).toEqual(['Synths', 'Samplers', 'Effects', 'Extensions', 'Other']);
    expect(groups[0]!.rows.map((r) => [r.name, r.on, r.complete])).toEqual([
      ['Alpha', false, false],
      ['Zeta', true, true],
    ]);
    expect(groups[3]!.rows[0]).toMatchObject({ id: 'seq', folder: 'ext-seq', extension: true, on: true });
  });
  it('leaves on/off unknown when the configuration could not be read', () => {
    expect(groupMods(held, undefined)[0]!.rows[0]!.on).toBeNull();
    expect(groupMods(held, null)[0]!.rows[0]!.on).toBe(false);
  });
});

describe('path rules', () => {
  it('allows changes anywhere under /yaffs2 except bootloader.dev and upgrade/ (1.13)', () => {
    expect(changeRefusal('/yaffs2/content/a.wav')).toBeNull();
    expect(changeRefusal('/yaffs2/xy-forever/notes.txt')).toBeNull();
    expect(changeRefusal('/yaffs2/firmware.version')).toBeNull();
    expect(changeRefusal('/yaffs2/xy-forever/Bootloader.ldr')).toBeNull();
    expect(changeRefusal('/etc/passwd')).toMatch(/Only files under \/yaffs2\//);
    expect(changeRefusal('/yaffs2/content/../x')).toMatch(/not allowed/);
    expect(changeRefusal('/yaffs2/BootLoader.dev')).toMatch(/never changed by hand/);
    expect(changeRefusal('/yaffs2/upgrade/x.tfw')).toMatch(/never changed by hand/);
    expect(changeRefusal('/yaffs2/UPGRADE')).toMatch(/never changed by hand/);
    expect(changeRefusal('/yaffs2/xy-forever/wt/a/b/c/d/x.txt')).toBeNull(); // #124: 6 folders
    expect(changeRefusal('/yaffs2/xy-forever/wt/a/b/c/d/e/x.txt')).toMatch(/more than 6 folders/);
    expect(changeRefusal('/yaffs2/xy-forever/wt/a/b/c/d', true)).toBeNull();
    expect(changeRefusal('/yaffs2/xy-forever/wt/a/b/c/d/e', true)).toMatch(/more than 6 folders/);
  });
  it('knows which folders accept changes', () => {
    expect(folderWritable('/yaffs2')).toBe(true);
    expect(folderWritable('/yaffs2/content')).toBe(true);
    expect(folderWritable('/yaffs2/content/presets/')).toBe(true);
    expect(folderWritable('/yaffs2/upgrade')).toBe(false);
    expect(folderWritable('/yaffs2/upgrade/sub')).toBe(false);
    expect(folderWritable('/etc')).toBe(false);
  });
  it('joins and climbs paths without leaving /yaffs2', () => {
    expect(joinPath('/yaffs2/content/', 'a')).toBe('/yaffs2/content/a');
    expect(parentOf('/yaffs2/content/a')).toBe('/yaffs2/content');
    expect(parentOf('/yaffs2')).toBe('/yaffs2');
  });
  it('checks typed names', () => {
    expect(nameProblem('ok.txt')).toBeNull();
    expect(nameProblem('a/b')).toMatch(/\//);
    expect(nameProblem(' ')).toMatch(/Type/);
  });
});
