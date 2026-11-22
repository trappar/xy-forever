"""Check necessary cold SPI lifecycle predicates; fixtures are not live state."""
import ctypes as C
from pathlib import Path
import tempfile
import unittest
import random

from xy_forever import hostbuild

U32=C.c_uint
OK,WAITING,CHOOSE,ADMIT,READY,UNAVAILABLE,IDENTITY,ROLE,LIFECYCLE,IRQ,BOUNDS,PROTOCOL,TIMEOUT,DRAIN,CLOSE=range(15)

class Interrupt(C.Structure):
    _fields_=[(name,U32) for name in ('id','index','handler','context','control')]

class Snapshot(C.Structure):
    _fields_=[(name,U32) for name in (
        'role','gpio_role','driver','vtable','spi','tx_dma','rx_dma','interrupt_mask','spi_control',
        'spi_status','spi_count','tx_config','rx_config','driver_phase','active_request','queue_head',
        'queue_tail','deferred_head','deferred_tail','gpio_table','gpio_slots')]+[
        ('gpio_matches',U32*2)]+[(name,U32) for name in
        ('tx_pending','rx_pending','direction','primary_state','pending_request','released','protocol_state')]+[
        ('interrupts',Interrupt*6)]


def configured(role,opened=False):
    s=Snapshot();s.role=s.gpio_role=role;s.driver=0x88080000;s.interrupt_mask=1<<11
    s.vtable=0x11906f24 if role else 0x119072f0
    s.spi=0x20055004 if role else 0x20057004
    s.tx_dma=0x20080000 if role else 0x20084000;s.rx_dma=s.tx_dma+0x1000
    s.primary_state=0 if role else 0x88090000
    s.gpio_table=0x11b00000;s.gpio_slots=6;s.gpio_matches[0]=s.gpio_matches[1]=1  # observed vector (Test022)
    ids=(0x26,0x24,0x25,0x27) if role else (0x2e,0x2c,0x2d,0x2f)
    handlers=(0x889f4f5e,0x889f4fa2,0x889f585c,0x11a00200) if role else (0x889f4f5e,0x889f4fa2,0x889f5dc6,0x889f64d0)
    for i in range(4):
        s.interrupts[i].id=ids[i]
        if opened:s.interrupts[i]=Interrupt(ids[i],i+1,handlers[i],s.driver,5)
    for i in range(4,6):s.interrupts[i]=Interrupt(0x10+i,i+1,0x88a303d4,0x88a306e8+(i-4)*0x34,5)
    if not role:s.interrupts[5]=Interrupt(0x15,0,0,0,0)
    if opened:s.spi_control=0x2000|(2 if role else 0)
    return s


class PeerServicesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary=tempfile.TemporaryDirectory(prefix='xyf-peer-services-')
        root=Path(__file__).resolve().parents[1];shared=Path(cls.temporary.name)/'services.dylib'
        cls.command=['/usr/bin/clang++','-std=c++11','-O2','-shared','-fPIC','-Wall','-Wextra','-Werror',
            '-ffreestanding','-fno-builtin','-fno-stack-protector','-fno-exceptions','-fno-rtti',
            *(str(root/p) for p in ('target/peer_services.cpp','native/peer_boot.cpp','native/hash.cpp',
                                    'native/inflate.cpp','target/xyf_crc32.cpp','tests/peer_services_bridge.cpp')),'-o',str(shared)]
        hostbuild.run(cls.command,check=True,capture_output=True);cls.lib=C.CDLL(str(shared))
        cls.lib.peer_services_check.argtypes=[U32,C.POINTER(Snapshot)];cls.lib.peer_services_check.restype=U32
        for name in ('dma_range','request_slot'):
            method=getattr(cls.lib,'peer_services_'+name);method.argtypes=[U32,U32];method.restype=U32
        cls.lib.peer_services_size.restype=cls.lib.peer_services_diagnostic_size.restype=U32
        cls.lib.peer_services_construct.argtypes=[C.c_void_p,C.c_void_p]
        cls.lib.peer_services_begin_host.argtypes=cls.lib.peer_services_ready.argtypes=[C.c_void_p]

    @classmethod
    def tearDownClass(cls):cls.temporary.cleanup()

    def check(self,s,which=0):return self.lib.peer_services_check(which,C.byref(s))

    def test_dma_ranges_reject_live_heap_and_both_module_stacks(self):
        valid=((0x8aa65c00,32),(0x8ab10000,65536),(0x8fffffe0,32))
        for address,count in valid:self.assertTrue(self.lib.peer_services_dma_range(address,count),(hex(address),count))
        invalid=((0x8a000000,32),(0x8a7fffe0,64),(0x8a800000,1),(0x8a80c000,32),
                 (0x8a80ffe0,64),(0x8aa65be0,32),(0x8aafffe0,64),(0x8ab00000,32),
                 (0x8ab0c000,32),(0x8ab0ffe0,64))
        for address,count in invalid:self.assertFalse(self.lib.peer_services_dma_range(address,count),(hex(address),count))
        # Exact half-open edges: heap end is unaligned, next aligned address is
        # admitted; relocated module start rejects only when actually touched.
        self.assertTrue(self.lib.peer_services_dma_range(0x8aa65c00,0x8ab00000-0x8aa65c00))
        self.assertFalse(self.lib.peer_services_dma_range(0x8aa65c00,0x8ab00001-0x8aa65c00))

    def test_dma_ranges_reject_alignment_zero_overflow_and_ram_boundaries(self):
        for address,count in ((0,32),(0x89ffffe0,32),(0x8ab10001,32),(0x8ab10000,0),
                              (0x8fffffe0,33),(0x90000000,1),(0xffffffe0,64),
                              (0x8ab10000,0xffffffff),(0xffffffff,1)):
            self.assertFalse(self.lib.peer_services_dma_range(address,count),(hex(address),count))
        for misalignment in range(1,32):
            self.assertFalse(self.lib.peer_services_dma_range(0x8ab10000+misalignment,1))

    def test_request_pool_exact_slots_match_original_valid_driver_predicate(self):
        rng=random.Random(0x706)
        for driver in (0x80000000,0x88080000,0x8ffffe34):
            for offset in range(-16,0x1dd):
                request=driver+offset
                original=(request>=driver+0x3c and request<driver+0x1bc and (request-driver-0x3c)%0x60==0)
                self.assertEqual(bool(self.lib.peer_services_request_slot(driver,request)),original,(hex(driver),offset))
        for _ in range(500):
            driver=rng.randrange(0x80000000,0x8ffffe35,4)
            slot=rng.choice((0x3c,0x9c,0xfc,0x15c))
            self.assertTrue(self.lib.peer_services_request_slot(driver,driver+slot))
            self.assertFalse(self.lib.peer_services_request_slot(driver,driver+slot+1))
        for driver in (0,0x7ffffffc,0x8ffffe38,0x90000000,0xfffffffc):
            self.assertFalse(self.lib.peer_services_request_slot(driver,(driver+0x3c)&0xffffffff))

    def test_both_roles_have_distinct_controller_dma_irq_identities(self):
        for role in (0,1):
            self.assertEqual(self.check(configured(role)),OK)
            self.assertEqual(self.check(configured(role,True),1),OK)
            s=configured(role);s.spi=0x20057004 if role else 0x20055004
            self.assertEqual(self.check(s),LIFECYCLE)

    def test_retained_role_checked_strap_only_recorded(self):
        # Test022: PA2 input buffer is disabled at cold time, so the strap reads 0 on both roles.
        for role in (0,1):
            s=configured(role);s.gpio_role=0;self.assertEqual(self.check(s),OK)
        s=configured(0);s.role=2;self.assertEqual(self.check(s),ROLE)

    def test_closed_check_rejects_live_or_deferred_requests(self):
        for name in ('spi_control','tx_config','rx_config','active_request','queue_head','queue_tail',
                     'deferred_head','deferred_tail','pending_request','tx_pending','rx_pending'):
            s=configured(1);setattr(s,name,1);self.assertEqual(self.check(s),LIFECYCLE,name)
        s=configured(1);s.interrupts[0].index=1;self.assertEqual(self.check(s),IRQ)

    def test_quiet_check_needs_the_hardware_off_not_the_driver_pointers(self):
        # Before b's stock reset. The master close (889f562e) never completes a request it
        # abandons, so the driver's pointers may still name it; SPI, DMA and the handlers must be off.
        for name in ('spi_control','tx_config','rx_config'):
            s=configured(1);setattr(s,name,1);self.assertEqual(self.check(s,3),LIFECYCLE,name)
        for i in range(4):
            s=configured(1);s.interrupts[i].index=1;self.assertEqual(self.check(s,3),IRQ,i)
        s=configured(1)
        for name in ('active_request','queue_head','deferred_head','deferred_tail','tx_pending','rx_pending'):
            setattr(s,name,1)
        self.assertEqual(self.check(s,3),OK)
        self.assertEqual(self.check(configured(1,True),3),LIFECYCLE)   # an open link is not quiet

    def test_reopening_requires_single_retained_gpio_callback_per_pin(self):
        for role in (0,1):
            for count in (0,2,0xffffffff):
                s=configured(role);s.gpio_matches[0]=count;self.assertEqual(self.check(s),IRQ)
        s=configured(1);s.gpio_matches[1]=0;self.assertEqual(self.check(s),IRQ)
        s=configured(1);s.gpio_slots=257;self.assertEqual(self.check(s),IRQ)

    def test_gpio_vector_location_l1_scratch_or_sdram_only(self):
        # Test022 refused because the vector (L1 scratch 0x11b00000) failed an SDRAM-only check.
        for role in (0,1):
            for table,expected in ((0x11b00000,OK),(0x880a0000,OK),(0x11b01ff0,IRQ),(0x11a00000,IRQ),
                                   (0x11900000,IRQ),(0x11b00002,IRQ)):
                s=configured(role);s.gpio_table=table;self.assertEqual(self.check(s),expected,(role,hex(table)))

    def test_exact_irq_handler_context_and_common_gpio_vector_required(self):
        for role in (0,1):
            for index in range(4):
                for field,value in (('index',0),('index',96),('handler',0x889f64d0),('context',0),('control',1)):
                    s=configured(role,True)
                    if field=='handler' and s.interrupts[index].handler==value:value^=2
                    setattr(s.interrupts[index],field,value)
                    self.assertEqual(self.check(s,1),IRQ,(role,index,field))
            s=configured(role);s.interrupts[4].handler=0;self.assertEqual(self.check(s),IRQ)
        s=configured(1);s.interrupt_mask=0;self.assertEqual(self.check(s),IRQ)

    def test_drain_waits_for_reply_callback_and_all_queues(self):
        for role in (0,1):
            s=configured(role,True);s.released=1
            self.assertEqual(self.check(s,2),OK)
            for field in ('active_request','queue_head','queue_tail','deferred_head','deferred_tail','pending_request','tx_pending'):
                probe=Snapshot.from_buffer_copy(s);setattr(probe,field,1)
                self.assertEqual(self.check(probe,2),WAITING,(role,field))
        s=configured(0,True);self.assertEqual(self.check(s,2),WAITING)
        s.released=1;s.driver_phase=1;self.assertEqual(self.check(s,2),WAITING)
        s.driver_phase=0;s.rx_pending=1;self.assertEqual(self.check(s,2),OK)

    def test_command_requests_cannot_be_confused_with_transfer_or_install_selects(self):
        is_command=self.lib.peer_services_command_request
        is_command.argtypes=[U32,U32,U32];is_command.restype=U32
        ack=self.lib.peer_services_command_ack;ack.argtypes=[U32];ack.restype=U32
        tag=0x434d
        for item in (0x03,0x10,0x11,0x13,0x2f,0x3f,0x64,0xff):
            for seq in (0,1,0xff):
                value=tag<<16|item<<8|seq
                self.assertTrue(is_command(0x18,value,0),hex(value))
                self.assertFalse(is_command(0x18,value,1))                 # a second argument is not a command
                for op in (1,7,8,9,10,0x11,0x12,0x19):self.assertFalse(is_command(op,value,0),(op,hex(value)))
                self.assertEqual(ack(value),value^0xffffffff)
                self.assertNotIn(ack(value),(0,1,2))                       # never a select result (ok, declined, veto)
        # The image transfer selects 1 and restores 0; b's install selects partition 0, 1 or 2;
        # the keepalive tag and neighbours of the command tag are not commands either.
        for value in (0,1,2,3,0xffff,0x4b410001,0x434c1001,0x434e1001,0xffffffff):
            self.assertFalse(is_command(0x18,value,0),hex(value))

    def test_host_cannot_claim_live_services_or_handoff(self):
        state=(C.c_uint64*((self.lib.peer_services_size()+7)//8))()
        diagnostic=(C.c_uint64*((self.lib.peer_services_diagnostic_size()+7)//8))()
        self.lib.peer_services_construct(state,diagnostic)
        self.assertFalse(self.lib.peer_services_ready(state))
        self.assertEqual(self.lib.peer_services_begin_host(state),UNAVAILABLE)
        self.assertFalse(self.lib.peer_services_ready(state))


if __name__=='__main__':unittest.main()
