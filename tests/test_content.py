import ctypes as C
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from xy_forever import hostbuild  # noqa: E402
Digest=C.c_ubyte*32
SIZE=C.CFUNCTYPE(C.c_bool,C.c_void_p,C.c_char_p,C.POINTER(C.c_bool),C.POINTER(C.c_uint))
READ=C.CFUNCTYPE(C.c_bool,C.c_void_p,C.c_char_p,C.c_uint,C.c_void_p,C.c_uint)
class Destination(C.Structure):_fields_=[('role',C.c_uint),('path',C.c_char*257)]
class Item(C.Structure):_fields_=[('index',C.c_uint),('size',C.c_uint),('destination',Destination),('digest',Digest)]
class Report(C.Structure):_fields_=[('kept',C.c_uint),('installed',C.c_uint),('removed',C.c_uint)]
CHANGE=C.CFUNCTYPE(C.c_bool,C.c_void_p,C.c_char_p,C.c_uint,C.POINTER(Digest))
class IO(C.Structure):_fields_=[('context',C.c_void_p),('size',SIZE),('target',READ),('staged',READ),('replace',CHANGE),('remove',CHANGE)]
def items(rows):
    return (Item*len(rows))(*(Item(i,len(data),Destination(4,path.encode()),
                Digest.from_buffer_copy(hashlib.sha256(data).digest())) for i,(path,data) in enumerate(rows)))

class Disk:
    def __init__(self,root):
        self.root=Path(root);self.events=[];self.fail_after_rename=False
        def target(path):return self.root/path.decode()
        def size(_,path,exists,count):
            file=target(path);exists[0]=file.exists();count[0]=file.stat().st_size if file.exists() else 0;return True
        def reader(base):
            def read(_,path,offset,out,count):
                file=self.root/base/path.decode()
                if not file.exists():return False
                with file.open('rb') as f:f.seek(offset);data=f.read(count)
                if len(data)!=count:return False
                C.memmove(out,data,count);return True
            return read
        def replace(_,path,size,digest):
            data=(self.root/'staged'/path.decode()).read_bytes()
            if len(data)!=size or hashlib.sha256(data).digest()!=bytes(digest[0]):return False
            tmp=self.root/'xy-forever/.content.new';tmp.parent.mkdir(parents=True,exist_ok=True)
            with tmp.open('wb') as f:f.write(data);f.flush();os.fsync(f.fileno())
            final=target(path);final.parent.mkdir(parents=True,exist_ok=True);os.replace(tmp,final)
            self.events.append(('replace',path.decode()))
            return not self.fail_after_rename
        def remove(_,path,size,digest):
            file=target(path);data=file.read_bytes()
            if len(data)!=size or hashlib.sha256(data).digest()!=bytes(digest[0]):return False
            file.unlink();self.events.append(('remove',path.decode()));return True
        self.callbacks=[SIZE(size),READ(reader('')),READ(reader('staged')),CHANGE(replace),CHANGE(remove)]
        self.io=IO(None,*self.callbacks)
    def put(self,path,data):
        file=self.root/path;file.parent.mkdir(parents=True,exist_ok=True);file.write_bytes(data)

class NativeContentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp=tempfile.TemporaryDirectory(prefix='xyf-content-');path=Path(cls.tmp.name)/'content.dylib'
        hostbuild.run(['/usr/bin/clang++','-std=c++11','-Wall','-Wextra','-Werror','-shared','-fPIC',
                       *[str(ROOT/'native'/n) for n in ('content.cpp','package_policy.cpp','hash.cpp')],
                       str(ROOT/'tests/content_bridge.cpp'),'-o',str(path)],check=True)
        cls.lib=C.CDLL(str(path));cls.lib.content_run.argtypes=[C.c_void_p,C.c_uint,C.c_void_p,C.c_uint,C.c_bool,C.c_void_p,C.c_void_p]
    @classmethod
    def tearDownClass(cls):cls.tmp.cleanup()
    def run_plan(self,disk,new,old=(),remove=False):
        report=Report();status=self.lib.content_run(items(new),len(new),items(old),len(old),remove,C.byref(disk.io),C.byref(report))
        return status,report
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
