"""Replay both processors with semantic stock storage requests, without I/O."""
import ctypes as C
import hashlib
from pathlib import Path
import struct
import sys
import tempfile
import unittest
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from xy_forever import hostbuild  # noqa: E402
from xy_forever.source_text import unit_text  # noqa: E402

U32=C.c_uint
BYTE=C.c_ubyte
PTR=C.POINTER(BYTE)
VOID=C.c_void_p
UPTR=C.POINTER(U32)
OK,WAITING,ARGUMENT,CAPACITY,PROTOCOL,IDENTITY,STOPPED=range(7)
STOPPED_PHASE,WAITING_PHASE,SELECT,METADATA,DATA,ADMIT,RESTORE,RELEASE,READY=range(9)


def buffer(data):
    return (BYTE*len(data)).from_buffer_copy(data)


class PeerBootTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory(prefix="xyf-peer-")
        root=Path(__file__).resolve().parents[1]
        cls.root=root
        lib=Path(cls.temporary.name)/"peer.dylib"
        cls.command=["/usr/bin/clang++","-std=c++11","-shared","-fPIC","-O2",
                     "-Wall","-Wextra","-Werror","-ffreestanding","-fno-builtin",
                     "-fno-stack-protector","-fno-exceptions","-fno-rtti",
                     *(str(root/"native"/name) for name in ("peer_boot.cpp","hash.cpp","inflate.cpp")),
                     str(root/"tests/peer_boot_bridge.cpp"),"-o",str(lib)]
        hostbuild.run(cls.command,check=True,capture_output=True)
        cls.lib=C.CDLL(str(lib))
        signatures={
            "encode_command":[U32,U32,U32,U32,PTR,U32,UPTR],
            "decode_command":[PTR,U32,UPTR,UPTR,UPTR,UPTR],
            "encode_result":[U32,U32,U32,PTR,U32,UPTR],
            "decode_result":[PTR,U32,U32,U32,UPTR],
            "server_construct":[VOID],"client_construct":[VOID],"server_begin":[VOID],
            "server_freeze":[VOID,PTR,U32,PTR],"server_freeze_hashing":[VOID,PTR,U32,PTR],
            "server_freeze_unhashed":[VOID,PTR,U32,U32],"client_init_unhashed":[VOID,PTR,U32],
            "server_process":[VOID,U32,U32,U32,PTR,U32,UPTR],
            "server_phase":[VOID],"server_ready":[VOID],
            "client_init":[VOID,PTR,U32],"client_request":[VOID,UPTR,UPTR,UPTR],
            "client_accept":[VOID,U32,PTR,U32],"client_phase":[VOID],
            "client_admit":[VOID,PTR],
            "client_ready":[VOID],"client_image_size":[VOID],"client_digest":[VOID,PTR],"client_crc":[VOID],
        }
        for name,args in signatures.items():
            f=getattr(cls.lib,"peer_"+name);f.argtypes=args
            f.restype=None if name.endswith(("construct","begin","digest")) else C.c_int
        cls.lib.peer_client_crc.restype=C.c_uint
        cls.lib.peer_server_size.restype=U32
        cls.lib.peer_client_size.restype=U32

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def new(self,kind):
        space=(C.c_uint64*((getattr(self.lib,"peer_"+kind+"_size")()+7)//8))()
        getattr(self.lib,"peer_"+kind+"_construct")(space)
        return space

    def test_stock_command_and_result_header_codecs(self):
        for request in ((7,0,0),(0x18,0,0),(0x18,1,0),(0x11,0,0),(0x12,1,128),(0x12,16385,1)):
            for sequence in (0,1,127,255):
                out=(BYTE*16)(*([0xA5]*16));n=U32()
                self.assertEqual(self.lib.peer_encode_command(*request,sequence,out,12,C.byref(n)),OK)
                self.assertEqual(bytes(out[:12]),struct.pack("<III",request[0]|sequence<<8,*request[1:]))
                self.assertEqual(bytes(out[12:]),b"\xA5"*4)
                op,a,b,seq=U32(),U32(),U32(),U32()
                self.assertEqual(self.lib.peer_decode_command(out,12,C.byref(op),C.byref(a),C.byref(b),C.byref(seq)),OK)
                self.assertEqual((op.value,a.value,b.value,seq.value),(*request,sequence))
                self.assertEqual(self.lib.peer_encode_result(request[0],sequence,0xdeadbeef,out,8,C.byref(n)),OK)
                self.assertEqual(bytes(out[:8]),struct.pack("<II",request[0]|sequence<<8,0xdeadbeef))
                result=U32()
                self.assertEqual(self.lib.peer_decode_result(out,8,request[0],sequence,C.byref(result)),OK)
                self.assertEqual(result.value,0xdeadbeef)
                self.assertEqual(self.lib.peer_decode_result(out,8,request[0],sequence^1,C.byref(result)),PROTOCOL)
        for request,sequence,capacity in (((0x13,1,1),0,12),((7,0,0),256,12),((7,0,0),0,11),((0x12,16385,2),0,12)):
            out=(BYTE*12)(*([0xA5]*12));n=U32(0xffffffff)
            self.assertNotEqual(self.lib.peer_encode_command(*request,sequence,out,capacity,C.byref(n)),OK)
            self.assertEqual(n.value,0);self.assertEqual(bytes(out),b"\xA5"*12)
        bad=buffer(struct.pack("<III",0x10007,0,0));op,a,b,seq=U32(),U32(),U32(),U32()
        self.assertEqual(self.lib.peer_decode_command(bad,12,C.byref(op),C.byref(a),C.byref(b),C.byref(seq)),PROTOCOL)

    def setup_pair(self,image,capacity=None):
        self.source=buffer(image)
        self.destination=(BYTE*(capacity if capacity is not None else ((len(image)>>9)+1)*512))()
        server=self.new("server");client=self.new("client")
        self.lib.peer_server_begin(server)
        self.assertEqual(self.lib.peer_server_freeze(server,self.source,len(image),buffer(hashlib.sha256(image).digest())),OK)
        self.assertEqual(self.lib.peer_client_init(client,self.destination,len(self.destination)),OK)
        return server,client

    def request(self,client):
        op,a,b=U32(),U32(),U32()
        self.assertEqual(self.lib.peer_client_request(client,C.byref(op),C.byref(a),C.byref(b)),OK)
        return op.value,a.value,b.value

    def serve(self,server,request,capacity=65536):
        guard=(BYTE*(capacity+32))(*([0xA5]*(capacity+32)))
        out=C.cast(C.byref(guard,16),PTR);size=U32(0xffffffff)
        status=self.lib.peer_server_process(server,*request,out,capacity,C.byref(size))
        self.assertEqual(bytes(guard[:16]),b"\xa5"*16)
        self.assertEqual(bytes(guard[16+capacity:]),b"\xa5"*16)
        if status:
            self.assertEqual(size.value,0)
            self.assertEqual(bytes(guard[16:16+capacity]),b"\xa5"*capacity)
        return status,bytes(guard[16:16+size.value])

    def step(self,server,client):
        if self.lib.peer_client_phase(client)==ADMIT:
            digest=hashlib.sha256(bytes(self.destination[:self.lib.peer_client_image_size(client)])).digest()
            self.assertEqual(self.lib.peer_client_admit(client,buffer(digest)),OK)
        request=self.request(client)
        status,data=self.serve(server,request)
        self.assertEqual(status,OK)
        self.assertEqual(self.lib.peer_client_accept(client,0,buffer(data),len(data)),OK)
        return request,data

    def advance(self,server,client,phase):
        while self.lib.peer_client_phase(client)!=phase:
            if phase==RESTORE and self.lib.peer_client_phase(client)==ADMIT:
                digest=hashlib.sha256(bytes(self.destination[:self.lib.peer_client_image_size(client)])).digest()
                self.assertEqual(self.lib.peer_client_admit(client,buffer(digest)),OK)
                continue
            self.step(server,client)

    def test_default_and_preselection_do_not_boot(self):
        server=self.new("server");client=self.new("client")
        self.assertEqual(self.serve(server,(7,0,0))[0],STOPPED)
        self.assertEqual(self.lib.peer_client_accept(client,0,None,0),STOPPED)
        self.assertFalse(self.lib.peer_client_ready(client))
        self.lib.peer_server_begin(server)
        for req in ((0x18,1,0),(0x11,0,0),(7,0,0)):
            self.assertEqual(self.serve(server,req)[0],WAITING)
            self.assertFalse(self.lib.peer_server_ready(server))

    def test_boundary_transfers_preserve_same_image_and_stock_padding(self):
        for size in (1,511,512,513,65535,65536,65537):
            with self.subTest(size=size):
                image=bytes((i*17+31)&255 for i in range(size))
                server,client=self.setup_pair(image)
                operations=[]
                while not self.lib.peer_client_ready(client):
                    req,data=self.step(server,client);operations.append(req)
                    if req[0]==0x11:
                        self.assertEqual(struct.unpack_from("<III",data),(3,size,zlib.crc32(image)))
                        self.assertEqual(data[12:16],b"\x01\x00\x00\x00")
                        self.assertEqual(data[16:28],b"CLPEER01\x01\x00\x00\x00")
                        self.assertEqual(data[28:60],hashlib.sha256(image).digest())
                self.assertTrue(self.lib.peer_server_ready(server))
                self.assertEqual(operations[:2],[(0x18,1,0),(0x11,0,0)])
                self.assertEqual(operations[-2:],[(0x18,0,0),(7,0,0)])
                self.assertEqual(bytes(self.destination[:size]),image)
                self.assertEqual(bytes(self.destination[size:]),bytes(len(self.destination)-size))
                self.assertEqual(self.lib.peer_client_image_size(client),size)
                digest=(BYTE*32)();self.lib.peer_client_digest(client,digest)
                self.assertEqual(bytes(digest),hashlib.sha256(image).digest())

    def test_full_eight_mib_limit_still_reserves_extra_sector(self):
        server,client=self.setup_pair(bytes(8*1024*1024))
        self.advance(server,client,ADMIT)
        self.assertEqual(self.lib.peer_client_image_size(client),8*1024*1024)
        self.assertEqual(len(self.destination),8*1024*1024+512)
        self.advance(server,client,READY)
        self.assertTrue(self.lib.peer_server_ready(server))

    def test_no_release_before_complete_read_and_restore(self):
        for phase in (SELECT,METADATA,DATA,ADMIT,RESTORE):
            server,client=self.setup_pair(bytes(70000))
            self.advance(server,client,phase)
            self.assertEqual(self.serve(server,(7,0,0))[0],PROTOCOL)
            self.assertFalse(self.lib.peer_server_ready(server))

    def test_peer_requires_separate_ldr_admission_before_release(self):
        server,client=self.setup_pair(bytes(513))
        self.advance(server,client,ADMIT)
        a,b,c=U32(),U32(),U32()
        self.assertEqual(self.lib.peer_client_request(client,C.byref(a),C.byref(b),C.byref(c)),WAITING)
        self.assertEqual(self.lib.peer_client_admit(client,buffer(bytes(32))),IDENTITY)
        self.assertFalse(self.lib.peer_server_ready(server))
        server,client=self.setup_pair(bytes(513));self.advance(server,client,ADMIT)
        self.destination[0]=1
        self.assertEqual(self.lib.peer_client_admit(client,buffer(hashlib.sha256(bytes(513)).digest())),IDENTITY)

    def test_wrong_or_changed_admitted_identity_stops_owner(self):
        server=self.new("server");self.lib.peer_server_begin(server)
        self.assertEqual(self.lib.peer_server_freeze(server,buffer(b"x"),1,buffer(bytes(32))),IDENTITY)
        self.assertEqual(self.lib.peer_server_phase(server),STOPPED_PHASE)
        # The owner's last refusal point is restore (stock op 7 is acknowledged
        # by the stock slave ISR itself, so a veto there would split the boot).
        server,client=self.setup_pair(bytes(513))
        self.advance(server,client,RESTORE)
        self.source[0]=1
        self.assertEqual(self.serve(server,(0x18,0,0))[0],IDENTITY)
        self.assertFalse(self.lib.peer_server_ready(server))
        server,client=self.setup_pair(bytes(513))
        self.advance(server,client,RELEASE)
        self.source[0]=1
        self.assertEqual(self.serve(server,(7,0,0))[0],OK)
        self.assertTrue(self.lib.peer_server_ready(server))

    def test_hashing_freeze_returns_identity_and_keeps_both_vetoes(self):
        image=bytes(range(256))*5
        server=self.new("server");self.lib.peer_server_begin(server)
        digest=buffer(bytes(32))
        self.assertEqual(self.lib.peer_server_freeze_hashing(server,buffer(image),len(image),digest),OK)
        self.assertEqual(bytes(digest),hashlib.sha256(image).digest())
        self.assertEqual(self.lib.peer_server_freeze_hashing(server,buffer(image),len(image),digest),STOPPED)
        # A hashing-frozen pair transfers and agrees like the checked freeze.
        self.source=buffer(image)
        self.destination=(BYTE*(((len(image)>>9)+1)*512))()
        server=self.new("server");client=self.new("client");self.lib.peer_server_begin(server)
        self.assertEqual(self.lib.peer_server_freeze_hashing(server,self.source,len(image),digest),OK)
        self.assertEqual(self.lib.peer_client_init(client,self.destination,len(self.destination)),OK)
        self.advance(server,client,READY)
        self.assertEqual(bytes(self.destination)[:len(image)],image)
        # The re-checks are CRC-32: one changed byte still stops owner (restore) and client (admit).
        server,client=self.setup_pair(image);self.advance(server,client,RESTORE)
        self.source[300]^=0x80
        self.assertEqual(self.serve(server,(0x18,0,0))[0],IDENTITY)
        server,client=self.setup_pair(image);self.advance(server,client,ADMIT)
        self.destination[300]^=0x80
        self.assertEqual(self.lib.peer_client_admit(client,buffer(hashlib.sha256(image).digest())),IDENTITY)

    def unhashed_pair(self,image,hashed_owner=False):
        self.source=buffer(image)
        self.destination=(BYTE*(((len(image)>>9)+1)*512))()
        server=self.new("server");client=self.new("client");self.lib.peer_server_begin(server)
        if hashed_owner:
            self.assertEqual(self.lib.peer_server_freeze(server,self.source,len(image),buffer(hashlib.sha256(image).digest())),OK)
        else:
            self.assertEqual(self.lib.peer_server_freeze_unhashed(server,self.source,len(image),zlib.crc32(image)),OK)
        self.assertEqual(self.lib.peer_client_init_unhashed(client,self.destination,len(self.destination)),OK)
        return server,client

    def test_unhashed_transfer_is_identified_by_size_and_crc(self):
        image=bytes((i*7+3)&0xff for i in range(70000))
        for hashed_owner in (False,True):              # an unhashed client accepts either owner
            server,client=self.unhashed_pair(image,hashed_owner)
            self.advance(server,client,ADMIT)
            received=buffer(bytes(32));self.lib.peer_client_digest(client,received)   # as file_boot: received_digest()
            self.assertEqual(bytes(received),hashlib.sha256(image).digest() if hashed_owner else bytes(32))
            self.assertEqual(self.lib.peer_client_admit(client,received),OK)
            self.advance(server,client,READY)
            self.assertEqual(bytes(self.destination)[:len(image)],image)
        # Corruption in transit: the CRC alone stops the client and the owner never releases.
        server,client=self.unhashed_pair(image)
        self.advance(server,client,DATA)
        _,data=self.serve(server,self.request(client));corrupt=bytearray(data);corrupt[42]^=1
        self.assertEqual(self.lib.peer_client_accept(client,0,buffer(corrupt),len(corrupt)),OK)
        while True:
            _,data=self.serve(server,self.request(client))
            status=self.lib.peer_client_accept(client,0,buffer(data),len(data))
            if status!=OK:break
        self.assertEqual(status,IDENTITY)
        self.assertFalse(self.lib.peer_server_ready(server))
        # Each copy is CRC-checked once (088+): an unhashed owner does not re-check at restore and an
        # unhashed client does not re-check at admit (both check their boot copy at hand-off).
        server,client=self.unhashed_pair(image);self.advance(server,client,ADMIT)
        self.assertEqual(self.lib.peer_client_admit(client,buffer(bytes(32))),OK)
        self.advance(server,client,RESTORE)
        self.source[300]^=0x80
        self.assertEqual(self.serve(server,(0x18,0,0))[0],OK)
        server,client=self.unhashed_pair(image);self.advance(server,client,ADMIT)
        self.destination[300]^=0x80
        self.assertEqual(self.lib.peer_client_admit(client,buffer(bytes(32))),OK)
        # The owner serves the CRC it was given (the caller's check of these bytes).
        server,client=self.unhashed_pair(image)
        self.advance(server,client,ADMIT)
        crc=self.lib.peer_client_crc(client)
        self.assertEqual(crc,zlib.crc32(image))
        # The hashed protocol keeps both re-checks.
        server,client=self.setup_pair(image);self.advance(server,client,RESTORE)
        self.source[300]^=0x80
        self.assertEqual(self.serve(server,(0x18,0,0))[0],IDENTITY)
        server,client=self.setup_pair(image);self.advance(server,client,ADMIT)
        self.destination[300]^=0x80
        self.assertEqual(self.lib.peer_client_admit(client,buffer(hashlib.sha256(image).digest())),IDENTITY)

    def test_corrupt_image_never_releases_owner(self):
        server,client=self.setup_pair(bytes(65537))
        self.advance(server,client,DATA)
        request=self.request(client);status,data=self.serve(server,request)
        corrupt=bytearray(data);corrupt[42]^=1
        self.assertEqual(self.lib.peer_client_accept(client,0,buffer(corrupt),len(corrupt)),OK)
        _,data=self.serve(server,self.request(client))
        self.assertEqual(self.lib.peer_client_accept(client,0,buffer(data),len(data)),IDENTITY)
        self.assertFalse(self.lib.peer_client_ready(client))
        self.assertFalse(self.lib.peer_server_ready(server))

    def test_wrong_extension_and_oversized_metadata_stop_client(self):
        for offset,value in ((0,2),(12,0),(13,1),(16,0),(24,2),(60,1)):
            server,client=self.setup_pair(bytes(1000))
            self.step(server,client)
            _,data=self.serve(server,self.request(client));bad=bytearray(data);bad[offset]=value
            self.assertNotEqual(self.lib.peer_client_accept(client,0,buffer(bad),len(bad)),OK)
            self.assertFalse(self.lib.peer_client_ready(client))
        server,client=self.setup_pair(bytes(512),capacity=512)
        self.step(server,client);_,data=self.serve(server,self.request(client))
        self.assertEqual(self.lib.peer_client_accept(client,0,buffer(data),len(data)),CAPACITY)

    def test_capacity_failure_does_not_advance_server(self):
        server,client=self.setup_pair(bytes(1000))
        self.step(server,client)
        self.assertEqual(self.serve(server,self.request(client),511)[0],CAPACITY)
        self.assertEqual(self.lib.peer_server_phase(server),METADATA)
        self.step(server,client)
        self.assertEqual(self.serve(server,self.request(client),1023)[0],CAPACITY)
        self.step(server,client)
        self.advance(server,client,READY)

    def test_repeated_sector_requests_and_select_are_idempotent(self):
        server,client=self.setup_pair(bytes(70000))
        req,data=self.step(server,client)
        self.assertEqual(self.serve(server,req),(OK,data))
        req,data=self.step(server,client)
        self.assertEqual(self.serve(server,req),(OK,data))
        req,data=self.step(server,client)
        self.assertEqual(self.serve(server,req),(OK,data))
        self.advance(server,client,READY)

    def test_unknown_write_out_of_order_and_oversized_requests_stop(self):
        for request in ((0x13,1,1),(0x12,2,1),(0x12,1,129),(0x12,0,1),(0x18,2,0)):
            server,client=self.setup_pair(bytes(70000))
            self.advance(server,client,DATA)
            self.assertEqual(self.serve(server,request)[0],PROTOCOL)
            self.assertFalse(self.lib.peer_server_ready(server))

    def test_device_error_length_error_and_padding_stop_client(self):
        server,client=self.setup_pair(bytes(513))
        self.assertEqual(self.lib.peer_client_accept(client,1,None,0),PROTOCOL)
        server,client=self.setup_pair(bytes(513))
        self.assertEqual(self.lib.peer_client_accept(client,0,buffer(b"x"),1),PROTOCOL)
        server,client=self.setup_pair(bytes(513));self.advance(server,client,DATA)
        _,data=self.serve(server,self.request(client));bad=bytearray(data);bad[-1]=1
        self.assertEqual(self.lib.peer_client_accept(client,0,buffer(bad),len(bad)),PROTOCOL)


if __name__=="__main__":
    unittest.main()


class SmallBlobCapacityTest(PeerBootTests):
    """Test 063: a CheckStatus blob (52 bytes) needs a whole-sector destination (062 failure)."""

    def test_small_blob_needs_a_sector(self):
        image = bytes(range(52))
        for capacity in (0x100, 511):
            self.source = buffer(image)
            self.destination = (BYTE * capacity)()
            client = self.new("client")
            self.assertNotEqual(self.lib.peer_client_init(client, self.destination, capacity), OK)
        root = Path(__file__).resolve().parents[1]
        hdr = (root / 'target/xyf_boot.hpp').read_text()
        import re
        cap = int(re.search(r'b_status_capacity = (0x[0-9a-f]+)u', hdr).group(1), 16)
        base = int(re.search(r'b_status_buffer = (0x[0-9a-f]+)u', hdr).group(1), 16)
        self.assertGreaterEqual(cap, 512)
        self.assertLessEqual(base + cap, 0x8b820000)                         # below b's staging
        server, client = self.setup_pair(image, capacity=cap)
        steps = 0
        while not self.lib.peer_client_ready(client) and steps < 20:
            self.step(server, client); steps += 1
        self.assertTrue(self.lib.peer_client_ready(client))
        self.assertEqual(bytes(self.destination[:52]), image)
        fb = unit_text(root / 'target/file_boot.cpp')
        self.assertIn('session.transfer(status, xyf_boot::b_status_capacity, true)', fb)
