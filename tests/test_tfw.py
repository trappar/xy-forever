import hashlib
import random
import struct
import subprocess
import unittest

import pytest

import xyf_hosttest

KEY=bytes(range(32))  # Public synthetic test vector, not a device key.
IV=bytes(range(16))

def container(payload,*,short=False,index=0,filename=b'firmware.zip',sku=0x84001,bad_padding=False):
    base=0x40 if short else 0x80
    clear=filename.ljust(256,b'\0')+payload
    if index==0xfe:
        cipher=clear
    else:
        padding=16-len(payload)%16
        clear+=bytes([0 if bad_padding else padding])*padding
        cipher=subprocess.run(['openssl','enc','-aes-256-cbc','-nopad','-K',KEY.hex(),'-iv',IV.hex()],
                              input=clear,capture_output=True,check=True).stdout
    header=bytearray(base+0x300)
    if not short:header[:4]=bytes.fromhex('babecafe')
    header[base-0x40:base-0x3c]=bytes.fromhex('beefcafe')
    header[base+4]=index;header[base+0x70:base+0x80]=IV
    struct.pack_into('<I4sHHI4HI',header,base+0x80,len(payload),b'TESG',1,1,sku,1,2,1,0,len(cipher))
    header[base+0x9c:base+0xbc]=hashlib.sha256(cipher).digest()
    return bytes(header)+cipher

@pytest.mark.gate
class NativeTfwTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lib=xyf_hosttest.module('tfw')

    def read(self,source,offset,count,key=KEY):
        return self.lib.read(source,key,offset,count)

    def test_both_wrappers_and_continuous_cbc_across_filename_and_random_reads(self):
        rng=random.Random(84001)
        for short in (False,True):
            for size in (1,15,16,17,511,512,513,8191):
                payload=rng.randbytes(size);source=container(payload,short=short)
                for offset,count in ((0,size),(size-1,1),(0,0),(min(9,size),max(0,size-min(9,size)))):
                    with self.subTest(short=short,size=size,offset=offset):
                        status,data,index,calls=self.read(source,offset,count)
                        self.assertEqual(status,0)
                        self.assertEqual(data,payload[offset:offset+count])
                        self.assertEqual((index,calls),(0,1))

    def test_clear_payload_never_requests_key(self):
        source=container(b'PK hello',index=0xfe)
        self.assertEqual(self.read(source,0,8,key=None),(0,b'PK hello',0,0))

    def test_index_one_uses_provider_without_embedded_keys(self):
        source=container(b'PK test',index=1)
        self.assertEqual(self.read(source,0,7),(0,b'PK test',1,1))

    def test_corruption_wrong_key_and_out_of_bounds_fail(self):
        source=container(b'PK'+bytes(range(200)))
        damaged=bytearray(source);damaged[-20]^=1
        self.assertEqual(self.read(bytes(damaged),0,2)[0],6)
        self.assertNotEqual(self.read(source,0,2,key=bytes(32))[0],0)
        self.assertEqual(self.read(source,202,1)[0],100)
        self.assertEqual(self.read(source,0,2,key=None)[0],7)
        self.assertEqual(self.read(container(b'data',bad_padding=True),0,1)[0],8)

    def test_malformed_manifest_sku_filename_padding_and_truncation_fail(self):
        variants=[container(b'data',sku=123),container(b'data',filename=b'../evil.zip'),
                  container(b'data',filename=b'no-zip-extension')]
        source=container(b'data')
        for at,value in ((0x104,ord('X')),(0x108,2),(0x118,0)):
            changed=bytearray(source);changed[at]=value;variants.append(bytes(changed))
        variants.extend((source[:-1],source[:300],b'unknown file'))
        for damaged in variants:
            with self.subTest(size=len(damaged)):
                self.assertNotEqual(self.read(damaged,0,1)[0],0)
