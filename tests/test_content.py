import hashlib
import os
from pathlib import Path
import tempfile
import unittest

import xyf_hosttest

def items(rows):
    return [(path,len(data),hashlib.sha256(data).digest()) for path,data in rows]

class Report:
    def __init__(self,kept,installed,removed):self.kept,self.installed,self.removed=kept,installed,removed

class Disk:
    """native/content.hpp's IO over a folder: the target tree at its root, staged files under staged/."""
    def __init__(self,root):
        self.root=Path(root);self.events=[];self.fail_after_rename=False
    def size(self,path):
        file=self.root/path;return file.stat().st_size if file.exists() else None
    def _read(self,file,offset,count):
        if not file.exists():return None
        with file.open('rb') as f:f.seek(offset);data=f.read(count)
        return data if len(data)==count else None
    def read_target(self,path,offset,count):return self._read(self.root/path,offset,count)
    def read_staged(self,path,offset,count):return self._read(self.root/'staged'/path,offset,count)
    def replace(self,path,size,digest):
        data=(self.root/'staged'/path).read_bytes()
        if len(data)!=size or hashlib.sha256(data).digest()!=digest:return False
        tmp=self.root/'xy-forever/.content.new';tmp.parent.mkdir(parents=True,exist_ok=True)
        with tmp.open('wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
        final=self.root/path;final.parent.mkdir(parents=True,exist_ok=True);os.replace(tmp,final)
        self.events.append(('replace',path))
        return not self.fail_after_rename
    def remove(self,path,size,digest):
        file=self.root/path;data=file.read_bytes()
        if len(data)!=size or hashlib.sha256(data).digest()!=digest:return False
        file.unlink();self.events.append(('remove',path));return True
    def put(self,path,data):
        file=self.root/path;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(data)

class NativeContentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.native=xyf_hosttest.module('content')
    def run_plan(self,disk,new,old=(),remove=False):
        status,*counts=self.native.run(items(new),items(old),remove,disk)
        return status,Report(*counts)
    def test_identical_stock_assets_are_kept_without_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            disk=Disk(directory);disk.put('content/display/screen.svg',b'same stock asset')
            status,report=self.run_plan(disk,[('content/display/screen.svg',b'same stock asset')])
            self.assertEqual(status,0);self.assertEqual(report.kept,1);self.assertEqual(disk.events,[])
    def test_known_previous_assets_replace_atomically_and_missing_assets_install(self):
        with tempfile.TemporaryDirectory() as directory:
            disk=Disk(directory);disk.put('content/display/screen.svg',b'old')
            disk.put('staged/content/display/screen.svg',b'new');disk.put('staged/content/display/new.svg',b'added')
            disk.put('projects/song.xy',b'user song')
            new=[('content/display/screen.svg',b'new'),('content/display/new.svg',b'added')]
            status,report=self.run_plan(disk,new,[('content/display/screen.svg',b'old')])
            self.assertEqual(status,0);self.assertEqual(report.installed,2)
            self.assertEqual((disk.root/'projects/song.xy').read_bytes(),b'user song')
    def test_modified_user_asset_aborts_entire_plan_before_any_writes(self):
        with tempfile.TemporaryDirectory() as directory:
            disk=Disk(directory);disk.put('content/one',b'old');disk.put('content/two',b'user edit')
            for path in ('one','two'):disk.put('staged/content/'+path,b'new')
            status,_=self.run_plan(disk,[('content/one',b'new'),('content/two',b'new')],
                                  [('content/one',b'old'),('content/two',b'old')])
            self.assertEqual(status,4);self.assertEqual(disk.events,[])
            self.assertEqual((disk.root/'content/one').read_bytes(),b'old')
    def test_all_changed_staged_hashes_are_checked_before_first_replace(self):
        with tempfile.TemporaryDirectory() as directory:
            disk=Disk(directory);disk.put('staged/content/one',b'new');disk.put('staged/content/two',b'corrupt')
            status,_=self.run_plan(disk,[('content/one',b'new'),('content/two',b'new')])
            self.assertEqual(status,6);self.assertEqual(disk.events,[])
    def test_power_loss_after_rename_can_resume_without_replacing_current_asset(self):
        with tempfile.TemporaryDirectory() as directory:
            disk=Disk(directory);disk.put('staged/content/one',b'new');disk.fail_after_rename=True
            self.assertEqual(self.run_plan(disk,[('content/one',b'new')])[0],7)
            disk.fail_after_rename=False;status,report=self.run_plan(disk,[('content/one',b'new')])
            self.assertEqual(status,0);self.assertEqual(report.kept,1);self.assertEqual(len(disk.events),1)
    def test_removal_only_deletes_matching_previous_canonical_content(self):
        with tempfile.TemporaryDirectory() as directory:
            disk=Disk(directory);disk.put('content/keep',b'new');disk.put('content/old-only',b'old')
            disk.put('content/presets/user.json',b'user preset')
            status,report=self.run_plan(disk,[('content/keep',b'new')],[('content/old-only',b'old')],remove=True)
            self.assertEqual(status,0);self.assertEqual(report.removed,1)
            self.assertEqual((disk.root/'content/presets/user.json').read_bytes(),b'user preset')
    def test_forged_destination_never_accesses_project_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            disk=Disk(directory)
            for path in ('projects/song.xy','content/../os.ldr','content/bootloader.bin'):
                self.assertEqual(self.run_plan(disk,[(path,b'data')])[0],3)
            self.assertEqual(disk.events,[])
