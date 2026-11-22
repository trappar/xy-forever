"""Replay both processors with semantic stock storage requests, without I/O."""
import hashlib
from pathlib import Path
import struct
import unittest
import zlib

import pytest

from xy_forever.source_text import unit_text

import xyf_hosttest

OK,WAITING,ARGUMENT,CAPACITY,PROTOCOL,IDENTITY,STOPPED=range(7)
STOPPED_PHASE,WAITING_PHASE,SELECT,METADATA,DATA,ADMIT,RESTORE,RELEASE,READY=range(9)


@pytest.mark.gate
class PeerBootTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib=xyf_hosttest.module("peer_boot")

    def new(self,kind):
        return getattr(self.lib,kind.capitalize())()

    def test_stock_command_and_result_header_codecs(self):
        for request in ((7,0,0),(0x18,0,0),(0x18,1,0),(0x11,0,0),(0x12,1,128),(0x12,16385,1)):
            for sequence in (0,1,127,255):
                status,n,out=self.lib.encode_command(*request,sequence,b"\xA5"*16,12)
                self.assertEqual(status,OK)
                self.assertEqual(out[:12],struct.pack("<III",request[0]|sequence<<8,*request[1:]))
                self.assertEqual(out[12:],b"\xA5"*4)
                status,*decoded=self.lib.decode_command(out[:12])
                self.assertEqual(status,OK)
                self.assertEqual(tuple(decoded),(*request,sequence))
                status,n,out=self.lib.encode_result(request[0],sequence,0xdeadbeef,out,8)
                self.assertEqual(status,OK)
                self.assertEqual(out[:8],struct.pack("<II",request[0]|sequence<<8,0xdeadbeef))
                self.assertEqual(self.lib.decode_result(out[:8],request[0],sequence),(OK,0xdeadbeef))
                self.assertEqual(self.lib.decode_result(out[:8],request[0],sequence^1)[0],PROTOCOL)
        for request,sequence,capacity in (((0x13,1,1),0,12),((7,0,0),256,12),((7,0,0),0,11),((0x12,16385,2),0,12)):
            status,n,out=self.lib.encode_command(*request,sequence,b"\xA5"*12,capacity)
            self.assertNotEqual(status,OK)
            self.assertEqual(n,0);self.assertEqual(out,b"\xA5"*12)
        bad=struct.pack("<III",0x10007,0,0)
        self.assertEqual(self.lib.decode_command(bad)[0],PROTOCOL)

    def setup_pair(self,image,capacity=None):
        self.source=bytearray(image)
        self.destination=bytearray(capacity if capacity is not None else ((len(image)>>9)+1)*512)
        server=self.new("server");client=self.new("client")
        server.begin()
        self.assertEqual(server.freeze(self.source,len(image),hashlib.sha256(image).digest()),OK)
        self.assertEqual(client.init(self.destination,len(self.destination)),OK)
        return server,client

    def request(self,client):
        status,*request=client.request()
        self.assertEqual(status,OK)
        return tuple(request)

    def serve(self,server,request,capacity=65536):
        status,size,guard=server.process(*request,capacity)
        self.assertEqual(guard[:16],b"\xa5"*16)
        self.assertEqual(guard[16+capacity:],b"\xa5"*16)
        if status:
            self.assertEqual(size,0)
            self.assertEqual(guard[16:16+capacity],b"\xa5"*capacity)
        return status,guard[16:16+size]

    def step(self,server,client):
        if client.phase()==ADMIT:
            digest=hashlib.sha256(bytes(self.destination[:client.image_size()])).digest()
            self.assertEqual(client.admit(bytes(digest)),OK)
        request=self.request(client)
        status,data=self.serve(server,request)
        self.assertEqual(status,OK)
        self.assertEqual(client.accept(0,bytes(data)),OK)
        return request,data

    def advance(self,server,client,phase):
        while client.phase()!=phase:
            if phase==RESTORE and client.phase()==ADMIT:
                digest=hashlib.sha256(bytes(self.destination[:client.image_size()])).digest()
                self.assertEqual(client.admit(bytes(digest)),OK)
                continue
            self.step(server,client)

    def test_default_and_preselection_do_not_boot(self):
        server=self.new("server");client=self.new("client")
        self.assertEqual(self.serve(server,(7,0,0))[0],STOPPED)
        self.assertEqual(client.accept(0,None),STOPPED)
        self.assertFalse(client.ready())
        server.begin()
        for req in ((0x18,1,0),(0x11,0,0),(7,0,0)):
            self.assertEqual(self.serve(server,req)[0],WAITING)
            self.assertFalse(server.ready())

    def test_boundary_transfers_preserve_same_image_and_stock_padding(self):
        for size in (1,511,512,513,65535,65536,65537):
            with self.subTest(size=size):
                image=bytes((i*17+31)&255 for i in range(size))
                server,client=self.setup_pair(image)
                operations=[]
                while not client.ready():
                    req,data=self.step(server,client);operations.append(req)
                    if req[0]==0x11:
                        self.assertEqual(struct.unpack_from("<III",data),(3,size,zlib.crc32(image)))
                        self.assertEqual(data[12:16],b"\x01\x00\x00\x00")
                        self.assertEqual(data[16:28],b"CLPEER01\x01\x00\x00\x00")
                        self.assertEqual(data[28:60],hashlib.sha256(image).digest())
                self.assertTrue(server.ready())
                self.assertEqual(operations[:2],[(0x18,1,0),(0x11,0,0)])
                self.assertEqual(operations[-2:],[(0x18,0,0),(7,0,0)])
                self.assertEqual(bytes(self.destination[:size]),image)
                self.assertEqual(bytes(self.destination[size:]),bytes(len(self.destination)-size))
                self.assertEqual(client.image_size(),size)
                self.assertEqual(client.digest(),hashlib.sha256(image).digest())

    def test_full_eight_mib_limit_still_reserves_extra_sector(self):
        server,client=self.setup_pair(bytes(8*1024*1024))
        self.advance(server,client,ADMIT)
        self.assertEqual(client.image_size(),8*1024*1024)
        self.assertEqual(len(self.destination),8*1024*1024+512)
        self.advance(server,client,READY)
        self.assertTrue(server.ready())

    def test_no_release_before_complete_read_and_restore(self):
        for phase in (SELECT,METADATA,DATA,ADMIT,RESTORE):
            server,client=self.setup_pair(bytes(70000))
            self.advance(server,client,phase)
            self.assertEqual(self.serve(server,(7,0,0))[0],PROTOCOL)
            self.assertFalse(server.ready())

    def test_peer_requires_separate_ldr_admission_before_release(self):
        server,client=self.setup_pair(bytes(513))
        self.advance(server,client,ADMIT)
        self.assertEqual(client.request()[0],WAITING)
        self.assertEqual(client.admit(bytes(32)),IDENTITY)
        self.assertFalse(server.ready())
        server,client=self.setup_pair(bytes(513));self.advance(server,client,ADMIT)
        self.destination[0]=1
        self.assertEqual(client.admit(hashlib.sha256(bytes(513)).digest()),IDENTITY)

    def test_wrong_or_changed_admitted_identity_stops_owner(self):
        server=self.new("server");server.begin()
        self.assertEqual(server.freeze(bytearray(b"x"),1,bytes(32)),IDENTITY)
        self.assertEqual(server.phase(),STOPPED_PHASE)
        # The owner's last refusal point is restore (stock op 7 is acknowledged
        # by the stock slave ISR itself, so a veto there would split the boot).
        server,client=self.setup_pair(bytes(513))
        self.advance(server,client,RESTORE)
        self.source[0]=1
        self.assertEqual(self.serve(server,(0x18,0,0))[0],IDENTITY)
        self.assertFalse(server.ready())
        server,client=self.setup_pair(bytes(513))
        self.advance(server,client,RELEASE)
        self.source[0]=1
        self.assertEqual(self.serve(server,(7,0,0))[0],OK)
        self.assertTrue(server.ready())

    def test_hashing_freeze_returns_identity_and_keeps_both_vetoes(self):
        image=bytes(range(256))*5
        server=self.new("server");server.begin()
        status,digest=server.freeze_hashing(bytearray(image),len(image))
        self.assertEqual(status,OK)
        self.assertEqual(digest,hashlib.sha256(image).digest())
        self.assertEqual(server.freeze_hashing(bytearray(image),len(image))[0],STOPPED)
        # A hashing-frozen pair transfers and agrees like the checked freeze.
        self.source=bytearray(image)
        self.destination=bytearray(((len(image)>>9)+1)*512)
        server=self.new("server");client=self.new("client");server.begin()
        self.assertEqual(server.freeze_hashing(self.source,len(image))[0],OK)
        self.assertEqual(client.init(self.destination,len(self.destination)),OK)
        self.advance(server,client,READY)
        self.assertEqual(bytes(self.destination)[:len(image)],image)
        # The re-checks are CRC-32: one changed byte still stops owner (restore) and client (admit).
        server,client=self.setup_pair(image);self.advance(server,client,RESTORE)
        self.source[300]^=0x80
        self.assertEqual(self.serve(server,(0x18,0,0))[0],IDENTITY)
        server,client=self.setup_pair(image);self.advance(server,client,ADMIT)
        self.destination[300]^=0x80
        self.assertEqual(client.admit(hashlib.sha256(image).digest()),IDENTITY)

    def unhashed_pair(self,image,hashed_owner=False):
        self.source=bytearray(image)
        self.destination=bytearray(((len(image)>>9)+1)*512)
        server=self.new("server");client=self.new("client");server.begin()
        if hashed_owner:
            self.assertEqual(server.freeze(self.source,len(image),hashlib.sha256(image).digest()),OK)
        else:
            self.assertEqual(server.freeze_unhashed(self.source,len(image),zlib.crc32(image)),OK)
        self.assertEqual(client.init(self.destination,len(self.destination),False),OK)
        return server,client

    def test_unhashed_transfer_is_identified_by_size_and_crc(self):
        image=bytes((i*7+3)&0xff for i in range(70000))
        for hashed_owner in (False,True):              # an unhashed client accepts either owner
            server,client=self.unhashed_pair(image,hashed_owner)
            self.advance(server,client,ADMIT)
            received=client.digest()   # as file_boot: received_digest()
            self.assertEqual(received,hashlib.sha256(image).digest() if hashed_owner else bytes(32))
            self.assertEqual(client.admit(received),OK)
            self.advance(server,client,READY)
            self.assertEqual(bytes(self.destination)[:len(image)],image)
        # Corruption in transit: the CRC alone stops the client and the owner never releases.
        server,client=self.unhashed_pair(image)
        self.advance(server,client,DATA)
        _,data=self.serve(server,self.request(client));corrupt=bytearray(data);corrupt[42]^=1
        self.assertEqual(client.accept(0,bytes(corrupt)),OK)
        while True:
            _,data=self.serve(server,self.request(client))
            status=client.accept(0,bytes(data))
            if status!=OK:break
        self.assertEqual(status,IDENTITY)
        self.assertFalse(server.ready())
        # Each copy is CRC-checked once (088+): an unhashed owner does not re-check at restore and an
        # unhashed client does not re-check at admit (both check their boot copy at hand-off).
        server,client=self.unhashed_pair(image);self.advance(server,client,ADMIT)
        self.assertEqual(client.admit(bytes(32)),OK)
        self.advance(server,client,RESTORE)
        self.source[300]^=0x80
        self.assertEqual(self.serve(server,(0x18,0,0))[0],OK)
        server,client=self.unhashed_pair(image);self.advance(server,client,ADMIT)
        self.destination[300]^=0x80
        self.assertEqual(client.admit(bytes(32)),OK)
        # The owner serves the CRC it was given (the caller's check of these bytes).
        server,client=self.unhashed_pair(image)
        self.advance(server,client,ADMIT)
        crc=client.crc()
        self.assertEqual(crc,zlib.crc32(image))
        # The hashed protocol keeps both re-checks.
        server,client=self.setup_pair(image);self.advance(server,client,RESTORE)
        self.source[300]^=0x80
        self.assertEqual(self.serve(server,(0x18,0,0))[0],IDENTITY)
        server,client=self.setup_pair(image);self.advance(server,client,ADMIT)
        self.destination[300]^=0x80
        self.assertEqual(client.admit(hashlib.sha256(image).digest()),IDENTITY)

    def test_corrupt_image_never_releases_owner(self):
        server,client=self.setup_pair(bytes(65537))
        self.advance(server,client,DATA)
        request=self.request(client);status,data=self.serve(server,request)
        corrupt=bytearray(data);corrupt[42]^=1
        self.assertEqual(client.accept(0,bytes(corrupt)),OK)
        _,data=self.serve(server,self.request(client))
        self.assertEqual(client.accept(0,bytes(data)),IDENTITY)
        self.assertFalse(client.ready())
        self.assertFalse(server.ready())

    def test_wrong_extension_and_oversized_metadata_stop_client(self):
        for offset,value in ((0,2),(12,0),(13,1),(16,0),(24,2),(60,1)):
            server,client=self.setup_pair(bytes(1000))
            self.step(server,client)
            _,data=self.serve(server,self.request(client));bad=bytearray(data);bad[offset]=value
            self.assertNotEqual(client.accept(0,bytes(bad)),OK)
            self.assertFalse(client.ready())
        server,client=self.setup_pair(bytes(512),capacity=512)
        self.step(server,client);_,data=self.serve(server,self.request(client))
        self.assertEqual(client.accept(0,bytes(data)),CAPACITY)

    def test_capacity_failure_does_not_advance_server(self):
        server,client=self.setup_pair(bytes(1000))
        self.step(server,client)
        self.assertEqual(self.serve(server,self.request(client),511)[0],CAPACITY)
        self.assertEqual(server.phase(),METADATA)
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
            self.assertFalse(server.ready())

    def test_device_error_length_error_and_padding_stop_client(self):
        server,client=self.setup_pair(bytes(513))
        self.assertEqual(client.accept(1,None),PROTOCOL)
        server,client=self.setup_pair(bytes(513))
        self.assertEqual(client.accept(0,b"x"),PROTOCOL)
        server,client=self.setup_pair(bytes(513));self.advance(server,client,DATA)
        _,data=self.serve(server,self.request(client));bad=bytearray(data);bad[-1]=1
        self.assertEqual(client.accept(0,bytes(bad)),PROTOCOL)

    def test_small_blob_needs_a_sector(self):
        """Test 063: a CheckStatus blob (52 bytes) needs a whole-sector destination (062 failure)."""
        image = bytes(range(52))
        for capacity in (0x100, 511):
            self.source = bytearray(image)
            self.destination = bytearray(capacity)
            client = self.new("client")
            self.assertNotEqual(client.init(self.destination, capacity), OK)
        root = Path(__file__).resolve().parents[1]
        hdr = (root / 'target/xyf_boot.hpp').read_text()
        import re
        cap = int(re.search(r'b_status_capacity = (0x[0-9a-f]+)u', hdr).group(1), 16)
        base = int(re.search(r'b_status_buffer = (0x[0-9a-f]+)u', hdr).group(1), 16)
        self.assertGreaterEqual(cap, 512)
        self.assertLessEqual(base + cap, 0x8b820000)                         # below b's staging
        server, client = self.setup_pair(image, capacity=cap)
        steps = 0
        while not client.ready() and steps < 20:
            self.step(server, client); steps += 1
        self.assertTrue(client.ready())
        self.assertEqual(bytes(self.destination[:52]), image)
        fb = unit_text(root / 'target/file_boot.cpp')
        self.assertIn('session.transfer(status, xyf_boot::b_status_capacity, true)', fb)


if __name__=="__main__":
    unittest.main()
