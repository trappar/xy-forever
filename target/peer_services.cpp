#include "cold_layout.hpp"
#include "peer_services.hpp"
#include "xyf_crc32.hpp"
#include "cold_heap.hpp"
namespace xy_forever { namespace peer_services {
namespace {
bool ram(U32 address,U32 count) {
    return !(address&3u)&&address>=0x80000000u&&count<=0x10000000u&&address<=0x90000000u-count;
}
// The retained GPIO callback vector (88a30650/54) lives in L1 data scratch at
// 0x11b00000 (Test022: both roles), not SDRAM. Data reads there are safe (the
// census reads it); L1 instruction memory 0x11a00000.. is never accepted.
bool gpio_vector(U32 address,U32 count) {
    if(ram(address,count))return true;
    return !(address&3u)&&address>=0x11b00000u&&count<=0x2000u&&address<=0x11b02000u-count;
}
bool owned_buffer_range(U32 address,U32 count) {
    if(address<0x8a000000u||address>=0x90000000u||!count||count>0x90000000u-address)return false;
    const U32 end=address+count;
    return !(address<cold_heap::arena_end&&cold_heap::arena_context<end)&&
           !(address<0x8a810000u&&0x8a800000u<end)&&
           !(address<XYF_STACK_END&&0x8ab00000u<end);
}
U32 expected_spi(U32 role) { return role?0x20055004u:0x20057004u; }
U32 expected_tx(U32 role) { return role?0x20080000u:0x20084000u; }
U32 expected_rx(U32 role) { return role?0x20081000u:0x20085000u; }
U32 expected_vtable(U32 role) { return role?0x11906f24u:0x119072f0u; }
const U32 slave_ids[4]={0x2e,0x2c,0x2d,0x2f};
const U32 master_ids[4]={0x26,0x24,0x25,0x27};
const U32 slave_handlers[4]={0x889f4f5e,0x889f4fa2,0x889f5dc6,0x889f64d0};
const U32 master_handlers[4]={0x889f4f5e,0x889f4fa2,0x889f585c,0x11a00200};
Result shape(const Snapshot& s) {
    // Role strap PA2 is readable only while its input buffer is enabled; the
    // stock sampler 88a1d510 enables it briefly and disables it again, so at
    // cold time it reads 0 on both roles (Test022). gpio_role is recorded only;
    // the role is confirmed by the role-specific vtable/SPI/DMA identities.
    if(s.role>1)return Result::role;
    if(!ram(s.driver,s.role?0xbcu:0x1ccu)||s.vtable!=expected_vtable(s.role)||
       s.spi!=expected_spi(s.role)||s.tx_dma!=expected_tx(s.role)||s.rx_dma!=expected_rx(s.role))
        return Result::lifecycle;
    if(!(s.interrupt_mask&(1u<<11)))return Result::irq;
    for(U32 i=0;i<4;++i)if(s.interrupts[i].id!=(s.role?master_ids[i]:slave_ids[i]))return Result::irq;
    for(U32 i=4;i<(s.role?6u:5u);++i) {
        const Interrupt& irq=s.interrupts[i];
        if(irq.id!=0x10u+i||!irq.index||irq.index>=96||irq.handler!=0x88a303d4u||
           irq.context!=0x88a306e8u+(i-4)*0x34u||(irq.control&5u)!=5u)
            return Result::irq;
    }
    if(!s.gpio_slots||s.gpio_slots>256||!gpio_vector(s.gpio_table,s.gpio_slots*16)||s.gpio_matches[0]!=1||
       (s.role&&s.gpio_matches[1]!=1))return Result::irq;
    if(!s.role&&!ram(s.primary_state,8))return Result::lifecycle;
    return Result::ok;
}
}
bool dma_buffer_range(U32 address,U32 count) {
    return !(address&31u)&&owned_buffer_range(address,count);
}
bool request_pool_slot(U32 driver,U32 request) {
    if(!ram(driver,0x1ccu))return false;
    return request==driver+0x3cu||request==driver+0x9cu||
           request==driver+0xfcu||request==driver+0x15cu;
}
bool command_request(U32 operation,U32 argument0,U32 argument1) {
    return operation==0x18u&&(argument0>>16)==Stock121PeerSession::command_tag&&!argument1;
}
bool boot_request(U32 vtable,U32 operation,U32 argument0,U32 argument1) {
    if(vtable==0x11907694u)return operation==0x18u&&!command_request(operation,argument0,argument1);
    return vtable==0x11907348u&&(operation==0x11u||operation==0x12u);
}
Result check_closed(const Snapshot& s) {
    Result result=shape(s);if(result!=Result::ok)return result;
    if(s.spi_control||s.tx_config||s.rx_config||s.active_request||s.queue_head||s.queue_tail||
       s.deferred_head||s.deferred_tail||s.pending_request||s.tx_pending||(s.role&&s.rx_pending))
        return Result::lifecycle;
    for(U32 i=0;i<4;++i)if(s.interrupts[i].index)return Result::irq;
    return Result::ok;
}
Result check_quiet(const Snapshot& s) {
    Result result=shape(s);if(result!=Result::ok)return result;
    if(s.spi_control||s.tx_config||s.rx_config)return Result::lifecycle;
    for(U32 i=0;i<4;++i)if(s.interrupts[i].index)return Result::irq;
    return Result::ok;
}
Result check_open(const Snapshot& s) {
    Result result=shape(s);if(result!=Result::ok)return result;
    if(!(s.spi_control&0x2000u)||bool(s.spi_control&2u)!=bool(s.role))return Result::lifecycle;
    for(U32 i=0;i<4;++i) {
        const Interrupt& irq=s.interrupts[i];
        if(!irq.index||irq.index>=96||irq.handler!=(s.role?master_handlers[i]:slave_handlers[i])||
           irq.context!=s.driver||(irq.control&5u)!=5u)return Result::irq;
    }
    return Result::ok;
}
Result check_drained(const Snapshot& s) {
    Result result=check_open(s);if(result!=Result::ok)return result;
    if(s.active_request||s.queue_head||s.queue_tail||s.deferred_head||s.deferred_tail||
       s.pending_request||s.tx_pending)return Result::waiting;
    if(!s.role&&(s.driver_phase||s.released!=1))return Result::waiting;
    // Slave RX can remain armed for the next command. The completed generic7
    // SPI-finish callback must have cleared active_request and rearmed phase0.
    return Result::ok;
}
Stock121PeerSession::Stock121PeerSession(Diagnostic& d):diagnostic_(d),scratch_(0),scratch_capacity_(0),
    driver_(0),primary_(0),timeout_(0),pending_(0),expected_bytes_(0),frozen_size_(0),opened_(false),guarded_(false),
    active_(false),failed_(false),agreed_(false),transferring_(false),handshake_only_(false),hold_(false),
    decline_(false),declined_(false),committed_(false),watch_(false),staged_(false),blob_(false),blob_done_(false),defer_close_(false),
    blobs_(0),
    idle_ms_(0),handshake_ms_(0),last_activity_(0),expected_echo_(0),echoes_(0),activity_(0),commands_seen_(0),
    last_command_(0),last_argument_(0),max_gap_(0),serve_(0),serve_context_(0),served_(0),served_activity_(false),rpc_(false),asking_(false),filing_(false),identity_ms_(0),
    peer_restart_(false),peer_request_(0),peer_ms_(0) {
    for(U32 i=0;i<3;++i)header_[i]=0;
    volatile Byte* bytes=reinterpret_cast<volatile Byte*>(&d);
    for(U32 i=0;i<sizeof(d);++i)bytes[i]=0;
    d.magic=0x52504558u;d.version=1;d.stage=Stage::stopped;d.result=Result::unavailable;
    for(U32 i=0;i<4;++i)saved_logs_[i]=0;
    for(U32 i=0;i<12;++i)request_[i]=0;
}
Result Stock121PeerSession::fail(Result result) {
    failed_=true;diagnostic_.stage=Stage::failed;diagnostic_.result=result;return result;
}
U32 Stock121PeerSession::role() const { return diagnostic_.before.role; }
U32 Stock121PeerSession::image_size() const { return role()?client_.image_size():frozen_size_; }
bool Stock121PeerSession::safe_to_handoff() const {
    return agreed_&&!failed_&&!opened_&&!guarded_&&diagnostic_.stage==Stage::closed;
}
void Stock121PeerSession::handshake_only() { if(!opened_&&!guarded_)handshake_only_=true; }
void Stock121PeerSession::hold(U32 idle_ms,U32 handshake_ms) {
    if(opened_||guarded_||!idle_ms||!handshake_ms)return;
    handshake_only_=hold_=true;idle_ms_=idle_ms;handshake_ms_=handshake_ms;
}
void Stock121PeerSession::serve(Serve handler,void* context) {
    if(opened_||guarded_||!hold_)return;
    serve_=handler;serve_context_=context;
}
U32 Stock121PeerSession::served() const { return served_; }
void Stock121PeerSession::stage(U32 idle_ms,U32 handshake_ms) {
    if(opened_||guarded_||!idle_ms||!handshake_ms)return;
    staged_=hold_=defer_close_=true;idle_ms_=idle_ms;handshake_ms_=handshake_ms;
}
bool Stock121PeerSession::agreed() const { return agreed_; }
void Stock121PeerSession::allow_close() { defer_close_=false; }
void Stock121PeerSession::refuse() { if(staged_&&!role()&&!agreed_)declined_=true; }
void Stock121PeerSession::touch(U32 now) { if(staged_&&!role())last_activity_=now; }
void Stock121PeerSession::follow(U32 request) {
    if(!peer_restart_){peer_restart_=true;peer_ms_=last_activity_;}
    if(!peer_request_)peer_request_=request;
}
bool Stock121PeerSession::follow_now(U32 now) const { return peer_restart_&&(peer_request_||now-peer_ms_>=follow_ms); }
void Stock121PeerSession::hold_touch(U32 now) { if(hold_&&!role())last_activity_=now; }
bool Stock121PeerSession::blob_done() const { return blob_done_; }
U32 Stock121PeerSession::blobs() const { return blobs_; }
Result Stock121PeerSession::freeze_blob(const Byte* data,U32 size) {
    if(!staged_||role()||!opened_||failed_||agreed_||declined_||server_.phase()!=xyf_core::peer::Phase::waiting)
        return Result::unavailable;
    xyf_core::hash::Digest digest;
    const Result frozen=freeze_image(data,size,xyf::crc::update(0,data,size),digest);
    if(frozen==Result::ok)blob_=true;
    return frozen;
}
Result Stock121PeerSession::transfer(Byte* destination,U32 capacity,bool blob) {
    if(!staged_||!role()||!link_ready()||active_||transferring_||agreed_||declined_)return Result::unavailable;
#if defined(__bfin__)
    const U32 first=reinterpret_cast<U32>(scratch_),second=reinterpret_cast<U32>(destination);
    if(!dma_buffer_range(second,capacity)||(first<second+capacity&&second<first+65536))return fail(Result::bounds);
#endif
    if(client_.initialize(destination,capacity,false)!=xyf_core::peer::Status::ok)return fail(Result::bounds);
    blob_=blob;blob_done_=false;transferring_=true;return Result::waiting;
}
void Stock121PeerSession::decline() { if(!opened_&&!guarded_&&!handshake_only_)decline_=true; }
void Stock121PeerSession::watch(U32 idle_ms) { if(!opened_&&!guarded_&&idle_ms){watch_=true;idle_ms_=idle_ms;} }
bool Stock121PeerSession::declined() const { return decline_||declined_; }
bool Stock121PeerSession::busy() const { return active_; }
U32 Stock121PeerSession::echoes() const { return echoes_; }
U32 Stock121PeerSession::activity() const { return activity_; }
U32 Stock121PeerSession::commands_seen() const { return commands_seen_; }
U32 Stock121PeerSession::last_command() const { return last_command_; }
U32 Stock121PeerSession::last_argument() const { return last_argument_; }
U32 Stock121PeerSession::max_gap() const { return max_gap_; }
bool Stock121PeerSession::link_ready() const { return opened_&&!failed_&&diagnostic_.link_initialized==1; }
Result Stock121PeerSession::start_receiving() {
    if(!role()||!link_ready()||active_||transferring_)return Result::unavailable;
    transferring_=true;return Result::ok;
}
Result Stock121PeerSession::freeze(const Byte* image,U32 size,const xyf_core::hash::Digest& admitted) {
    if(!opened_||failed_||role()||agreed_||handshake_only_||decline_)return Result::unavailable;
#if defined(__bfin__)
    const U32 begin=reinterpret_cast<U32>(image),scratch=reinterpret_cast<U32>(scratch_);
    if(!size||size>xyf_core::peer::maximum_image||!owned_buffer_range(begin,size)||
       (begin<scratch+65536&&scratch<begin+size))return fail(Result::bounds);
#endif
    if(server_.freeze(image,size,admitted)!=xyf_core::peer::Status::ok)return fail(Result::identity);
    frozen_size_=size;
    diagnostic_.stage=Stage::serving;return Result::ok;
}
Result Stock121PeerSession::freeze_image(const Byte* image,U32 size,U32 crc,xyf_core::hash::Digest& digest) {
    if(!opened_||failed_||role()||agreed_||handshake_only_||decline_)return Result::unavailable;
#if defined(__bfin__)
    const U32 begin=reinterpret_cast<U32>(image),scratch=reinterpret_cast<U32>(scratch_);
    if(!size||size>xyf_core::peer::maximum_image||!owned_buffer_range(begin,size)||
       (begin<scratch+65536&&scratch<begin+size))return fail(Result::bounds);
#endif
    // Boot transfers are identified by size and CRC-32 (info.txt); no SHA-256 (~0.7 s per pass).
    digest=xyf_core::hash::Digest();
    if(server_.freeze_unhashed(image,size,crc)!=xyf_core::peer::Status::ok)return fail(Result::identity);
    frozen_size_=size;
    diagnostic_.stage=Stage::serving;return Result::ok;
}
Result Stock121PeerSession::admit(const xyf_core::hash::Digest& admitted) {
    if(!opened_||failed_||!role()||client_.phase()!=xyf_core::peer::Phase::admit)return Result::unavailable;
    if(client_.admit(admitted)!=xyf_core::peer::Status::ok)return fail(Result::identity);
    diagnostic_.stage=Stage::receiving;return Result::ok;
}
#if defined(__bfin__)
namespace {
// One noinline, noclone instantiation per stock entry: its address load sits
// beside the jump, which keeps the builder's straight-line call audit exact.
template<U32 A,class R,class... Args> __attribute__((noinline,noclone)) R stock(Args... args) {
    return reinterpret_cast<R(*)(Args...)>(A)(args...);
}
volatile U32& word(U32 address){return *reinterpret_cast<volatile U32*>(address);}
volatile Byte& byte(U32 address){return *reinterpret_cast<volatile Byte*>(address);}
volatile U32 owner=0xffffffffu; // initialized data; the cold link has no CRT.
volatile U32 stale_deferred_node __attribute__((section(".data")))=0;     // first (cold entry)
volatile U32 stale_deferred_latest __attribute__((section(".data")))=0;     // latest (e.g. after release 7)
U32 crc(U32 address,U32 count) {
    U32 value=0xffffffffu;
    for(U32 i=0;i<count;++i){value^=byte(address+i);for(U32 b=0;b<8;++b)value=(value>>1)^(0xedb88320u&(0u-(value&1u)));}
    return ~value;
}
bool identity() {
    // Never L1 instruction SRAM (0x11a00000..0x11a0c000): cold core data
    // loads there stalled Tests 002/005/006. Mutable data inside stock code
    // ranges is excluded: 0x88a31134..40 (0x88a3113c differs at every cold
    // entry), 0x88a3187c..0x88a318fc (byte store 88a312c6) and the counter
    // 0x88a33f0c (differs at every cold entry, Tests 017-020).
    const U32 spans[][3]={
        {0x889f4d00u,0x4600u,0x291f969du},
        {0x11906eecu,0xbc8u,0x05c25906u},
        {0x88a30200u,0x448u,0xa4d14bc8u},
        {0x88a306e8u,0x9cu,0x1c0ab177u},
        {0x88a30798u,0x99cu,0x9d6a2bc5u},
        {0x88a31140u,0x73cu,0x1b6aab65u},
        {0x88a318fcu,0x1c4u,0xc2de52e7u},
        {0x88a33d00u,0x20cu,0x3f17e000u},
        {0x88a33f10u,0x2f0u,0x07cc8d5eu}
    };
    if(word(0x88a2a4c8u)!=96||word(0x88a2a4ccu)!=96)return false;
    for(U32 i=0;i<sizeof(spans)/sizeof(spans[0]);++i)if(crc(spans[i][0],spans[i][1])!=spans[i][2])return false;
    return true;
}
bool logs_muted(){for(U32 i=0;i<4;++i)if(word(0x8803d06cu+4*i))return false;return true;}
bool dma_buffer(const Byte* buffer,U32 count) {
    return dma_buffer_range(reinterpret_cast<U32>(buffer),count);
}
void interrupt(Interrupt& irq,U32 id) {
    irq.id=id;irq.index=byte(0x11b00ac4u+id);irq.handler=irq.context=0;
    irq.control=word(0x20004800u+id*8);
    if(irq.index&&irq.index<96){irq.handler=word(0x11b008d0u+irq.index*4);irq.context=word(0x11b005d4u+irq.index*8);}
}
bool gpio_callbacks(Snapshot& s) {
    s.gpio_table=word(0x88a30650u);s.gpio_slots=word(0x88a30654u);
    s.gpio_matches[0]=s.gpio_matches[1]=0;
    if(!s.gpio_slots||s.gpio_slots>256||!gpio_vector(s.gpio_table,s.gpio_slots*16))return false;
    for(U32 i=0;i<s.gpio_slots;++i){
        const U32 entry=s.gpio_table+i*16,group=word(entry),mask=word(entry+4);
        const U32 callback=word(entry+8),context=word(entry+12);
        if(!callback)continue;
        const U32 expected_group[2]={s.role?1u:0u,0};
        const U32 expected_mask[2]={s.role?0x20u:0x8000u,0x400000};
        const U32 expected_callback[2]={s.role?0x11a00664u:0x889f6244u,0x11a00458};
        for(U32 k=0;k<(s.role?2u:1u);++k)if(group==expected_group[k]&&(mask&expected_mask[k])){
            if(mask!=expected_mask[k]||callback!=expected_callback[k]||context!=s.driver)return false;
            ++s.gpio_matches[k];
        }
    }
    return true;
}
// The stock close methods leave pin callbacks registered. Reopening via the
// full stock open method would append duplicates. Reuse only the exact single
// retained record checked above, restoring its edge/mask without appending it.
void gpio_reuse(U32 port,U32 mask,U32 group,U32 edge) {
    stock<0x11a04f78u,void>(port,mask,0);
    if(port==group)mask<<=16;
    stock<0x88a304c4u,U32>(group,mask,edge);
    stock<0x88a3046cu,U32>(group,mask,1);
}
// Port of the nonblocking53ee..5590 setup prefix. The two synchronous1/10
// exchanges in the stock suffix are submitted/polled separately below.
void master_setup(U32 driver,U32* temporary) {
    byte(driver+0x34)=0;
    stock<0x889f533au,void>(temporary,0x1e);
    stock<0x889f53aeu,void>(driver+0x38,temporary);
    stock<0x889f537au,void>(temporary);
    stock<0x889f533au,void>(temporary,0x1f);
    stock<0x889f53aeu,void>(driver+0x68,temporary);
    stock<0x889f537au,void>(temporary);
    stock<0x889f52f4u,void>(temporary,6);
    stock<0x889f4e3eu,void>(driver+0x98,temporary);
    stock<0x889f52c0u,void>(temporary);
    byte(driver+0x9e)=0xff;word(driver+0xb8)=word(driver+0xb4)=0;
    word(0x20040108u)=0x800;word(0x20040110u)=0x800;
    stock<0x11a04f78u,void>(2,0x800,1);
    stock<0x11a04f78u,void>(0,0x40,0);
    stock<0x11a04f78u,void>(2,0x20,0);
    word(driver+0x1c)=0x26;word(driver+0x14)=0x25;word(driver+0x18)=0x24;
    word(driver+8)=0x20055004;word(driver+0xc)=0x20080000;
    word(driver+0x20)=0x27;word(driver+0x10)=0x20081000;
    stock<0x88a30798u,U32>(0x27,0x11a00200,driver,1);
    stock<0x88a30798u,U32>(0x25,0x889f585c,driver,1);
    stock<0x889f5010u,void>(driver);
    const U32 spi=0x20055004;
    word(spi+0xc)=3;word(spi)|=0x2082;word(spi+0x10)=0;word(spi+4)|=8;
    gpio_reuse(2,0x20,1,1);
    gpio_reuse(0,0x40,0,1);
    word(0x20041004u)=0x400000;word(0x20041024u)=0x400000;
    stock<0x88a319fau,U32>(0x14,1);
    stock<0x88a319fau,U32>(0x15,1);
}
void slave_setup(U32 driver) {
    byte(driver+0x34)=0;
    stock<0x889f5db0u,void>(0,0x10);
    stock<0x11a04f78u,void>(0,0x10,1);
    stock<0x889f5db0u,void>(1,4);
    stock<0x11a04f78u,void>(1,4,1);
    stock<0x11a04f78u,void>(1,0x8000,0);
    word(driver+0x10)=0x20085000;word(driver+0x1c)=0x2e;
    word(driver+8)=0x20057004;word(driver+0xc)=0x20084000;
    word(driver+0x20)=0x2f;word(driver+0x14)=0x2d;word(driver+0x18)=0x2c;
    stock<0x889f5010u,void>(driver);
    stock<0x88a30798u,U32>(0x2d,0x889f5dc6,driver,1);
    stock<0x88a30798u,U32>(0x2f,0x889f64d0,driver,1);
    gpio_reuse(1,0x8000,0,0);
    word(0x20041004u)=0x8000;word(0x20041024u)=0x8000;
    stock<0x88a319fau,U32>(0x14,1);
    const U32 spi=0x20057004;
    word(spi)|=0x2000;word(spi+0x54)=0;word(spi+8)=(word(spi+8)&0xffffff82u)|0x101;
    stock<0x889f626cu,void>(driver);
}
}
bool Stock121PeerSession::capture(Snapshot& s) { return identity()&&capture_state(s); }
bool Stock121PeerSession::capture_state(Snapshot& s) {
    s.role=byte(0x8807cac4u);s.gpio_role=(word(0x2004000cu)>>2)&1;
    if(s.role>1)return false;
    s.driver=word(s.role?0x11b00060u:0x11b00064u);
    if(!ram(s.driver,s.role?0xbc:0x1cc))return false;
    s.vtable=word(s.driver);s.spi=word(s.driver+8);s.tx_dma=word(s.driver+0xc);s.rx_dma=word(s.driver+0x10);
    if(s.spi!=expected_spi(s.role)||s.tx_dma!=expected_tx(s.role)||s.rx_dma!=expected_rx(s.role))return false;
    s.interrupt_mask=word(0x1fc02104u);s.spi_control=word(s.spi);s.spi_status=word(s.spi+0x40);s.spi_count=word(s.spi+0x20);
    s.tx_config=word(s.tx_dma+8);s.rx_config=word(s.rx_dma+8);
    s.driver_phase=*reinterpret_cast<volatile unsigned short*>(s.driver+4);
    s.active_request=word(s.driver+(s.role?0xb0:0x30));
    s.queue_head=word(s.driver+(s.role?0xac:0x1c4));s.queue_tail=s.role?0:word(s.driver+0x1c8);
    s.deferred_head=s.role?word(s.driver+0xb4):0;s.deferred_tail=s.role?word(s.driver+0xb8):0;
    if(s.role&&s.deferred_head&&s.deferred_head==s.deferred_tail&&!s.active_request&&
       ram(s.deferred_head,0x10)&&!word(s.deferred_head+0xc)&&!byte(s.driver+0x28)){
        if(!stale_deferred_node)stale_deferred_node=s.deferred_head;
        stale_deferred_latest=s.deferred_head;s.deferred_head=s.deferred_tail=0;
    }
    s.tx_pending=byte(s.driver+0x28);s.rx_pending=byte(s.driver+0x29);s.direction=word(s.driver+0x38);
    s.protocol_state=byte(s.driver+0x34);
    s.primary_state=s.role?0:word(0x8804f6e0u);s.pending_request=s.released=0;
    if(!s.role){if(!ram(s.primary_state,8))return false;s.pending_request=word(s.primary_state+4);s.released=byte(s.primary_state);}
    for(U32 i=0;i<4;++i)interrupt(s.interrupts[i],s.role?master_ids[i]:slave_ids[i]);
    interrupt(s.interrupts[4],0x14);interrupt(s.interrupts[5],0x15);
    return gpio_callbacks(s);
}
Result Stock121PeerSession::begin(services::Phase phase,Byte* scratch,U32 capacity,Byte* destination,U32 destination_capacity,U32 now,U32 timeout) {
    if(phase!=services::Phase::cold_after_teardown||opened_||guarded_||failed_||owner!=0xffffffffu)return fail(Result::unavailable);
    if(!timeout||(timeout>600000&&timeout!=no_cap)||capacity<65536||!dma_buffer(scratch,capacity))return fail(Result::bounds);
    if(!capture(diagnostic_.before))return fail(Result::identity);
    diagnostic_.stage=Stage::captured;
    Result checked=check_closed(diagnostic_.before);if(checked!=Result::ok)return fail(checked);
    if(role()&&(!dma_buffer(destination,destination_capacity)||client_.initialize(destination,destination_capacity,false)!=xyf_core::peer::Status::ok))return fail(Result::bounds);
    if(role()){
        const U32 first=reinterpret_cast<U32>(scratch),second=reinterpret_cast<U32>(destination);
        if(first<second+destination_capacity&&second<first+65536)return fail(Result::bounds);
    }
    scratch_=scratch;scratch_capacity_=capacity;driver_=diagnostic_.before.driver;primary_=diagnostic_.before.primary_state;
    timeout_=timeout;diagnostic_.started_ms=now;diagnostic_.elapsed_ms=0;identity_ms_=now;
    owner=reinterpret_cast<U32>(this);guarded_=true;
    for(U32 i=0;i<4;++i){saved_logs_[i]=word(0x8803d06cu+4*i);word(0x8803d06cu+4*i)=0;}
    diagnostic_.stage=Stage::opening;opened_=true;
    if(!role()){
        byte(primary_)=0;word(primary_+4)=0;server_.begin();
        slave_setup(driver_);diagnostic_.stage=Stage::serving;
        for(U32 i=0;i<3;++i)header_[i]=word(0x11b000c0u+4*i);
        last_activity_=now;
    } else {
        master_setup(driver_,request_);diagnostic_.stage=Stage::handshake1;
        stock<0x889f4e82u,void>(request_,1,0);
        stock<0x11a0060cu,void>(driver_,request_);active_=true;
    }
    if(!capture(diagnostic_.after))return fail(Result::identity);
    checked=check_open(diagnostic_.after);if(checked!=Result::ok)return fail(checked);
    diagnostic_.result=Result::waiting;return Result::waiting;
}
// Role0: the stock slave DMAs each request header into 0x11b000c0 (armed by
// 889f626c via 11a006e6(0x11b000c0,12)); replies use 0x11b00080. Two equal
// reads reject a header the DMA is still writing.
void Stock121PeerSession::observe(U32 now) {
    U32 first[3],second[3];
    for(U32 i=0;i<3;++i)first[i]=word(0x11b000c0u+4*i);
    for(U32 i=0;i<3;++i)second[i]=word(0x11b000c0u+4*i);
    bool stable=true,changed=false;
    for(U32 i=0;i<3;++i){if(first[i]!=second[i])stable=false;if(first[i]!=header_[i])changed=true;}
    if(!stable||!changed)return;
    for(U32 i=0;i<3;++i)header_[i]=first[i];
    const U32 gap=now-last_activity_;if(gap>max_gap_)max_gap_=gap;
    last_activity_=now;++activity_;
    if((first[0]&0xffu)==10u){
        last_argument_=first[1];
        if((first[1]>>16)==command_tag){++commands_seen_;last_command_=first[1];}
        else if(first[1]==handshake_value&&staged_&&commands_seen_&&!peer_restart_){peer_restart_=true;peer_ms_=now;}   // B bootloader seen
    }
}
Result Stock121PeerSession::command(U32 value) {
    if(!hold_||!role()||!link_ready()||active_||agreed_||transferring_)return Result::unavailable;
    for(U32 i=0;i<12;++i)request_[i]=0;
    stock<0x889f4e82u,void>(request_,10,value);expected_echo_=~value;
    diagnostic_.last_operation=10;++diagnostic_.requests;
    active_=true;stock<0x11a0060cu,void>(driver_,request_);return Result::waiting;
}
Result Stock121PeerSession::ask(U32 value) {
    if(!staged_||!role()||!link_ready()||active_||agreed_||declined_||transferring_||
       !command_request(0x18,value,0))return Result::unavailable;
    const xyf_core::peer::Request request={0x18,value,0};
    expected_echo_=command_ack(value);asking_=true;return submit(request);
}
bool Stock121PeerSession::answering() const {
    if(!opened_||role())return false;
    return word(driver_+0x30)||word(driver_+0x1c4)||word(driver_+0x1c8)||byte(driver_+0x28);
}
Result Stock121PeerSession::release() {
    if(!hold_||!role()||!link_ready()||active_||agreed_)return Result::unavailable;
    xyf_core::peer::Request release={7,0,0};return submit(release);
}
U32* Stock121PeerSession::request_slot() {
    if(!staged_||!role()||!link_ready()||active_||agreed_||declined_||transferring_)return 0;
    for(U32 i=0;i<12;++i)request_[i]=0;
    return request_;
}
Result Stock121PeerSession::send_request(U32 operation) {
    if(!staged_||!role()||!link_ready()||active_||agreed_||declined_||transferring_)return Result::unavailable;
    diagnostic_.last_operation=operation;++diagnostic_.requests;
    *(reinterpret_cast<volatile Byte*>(request_)+4)=0;                  // as 889f7b12, before the submit
    filing_=active_=true;stock<0x11a0060cu,void>(driver_,request_);return Result::waiting;
}
Result Stock121PeerSession::submit(const xyf_core::peer::Request& req) {
    if(active_)return fail(Result::lifecycle);
    for(U32 i=0;i<12;++i)request_[i]=0;
    expected_bytes_=req.operation==0x11?512:req.operation==0x12?req.argument1*512:0;
    if(expected_bytes_>scratch_capacity_)return fail(Result::bounds);
    if(req.operation==7)stock<0x889f65a4u,void>(request_,7);
    else if(req.operation==0x18)stock<0x889f87d4u,void>(request_,0x18,req.argument0,0,0);
    else if(req.operation==0x11||req.operation==0x12)
        stock<0x889f8872u,void>(request_,req.operation,req.argument0,req.argument1,0,scratch_,expected_bytes_);
    else return fail(Result::protocol);
    diagnostic_.last_operation=req.operation;++diagnostic_.requests;
    active_=true;stock<0x11a0060cu,void>(driver_,request_);return Result::waiting;
}
void Stock121PeerSession::reply(U32 request,U32 result,U32 error) {
    if(!opened_||role()||!request_pool_slot(driver_,request))return;
    word(request+0x10)=result;word(request+0x14)=error;
    diagnostic_.last_operation=byte(request+5);diagnostic_.last_device_result=result;++diagnostic_.responses;
    stock<0x889f6508u,void>(driver_,request);
}
// Role0: answer the current pending request with a refusal and no data body
// (partition requests only; the stock executor 889f76ca also only stores its
// result at +0x10 before queuing the response with 889f672a -> 889f6508).
void Stock121PeerSession::respond(U32 result) {
    word(pending_+0x10)=result;word(pending_+0x14)=0;
    diagnostic_.last_operation=byte(pending_+5);diagnostic_.last_device_result=result;++diagnostic_.responses;
    stock<0x889f6508u,void>(driver_,pending_);pending_=0;
}
Result Stock121PeerSession::poll(U32 now) {
    if(safe_to_handoff())return Result::ready;
    if(failed_||!opened_||owner!=reinterpret_cast<U32>(this)||!logs_muted())return fail(Result::lifecycle);
    diagnostic_.elapsed_ms=now-diagnostic_.started_ms;
    if(timeout_!=no_cap&&diagnostic_.elapsed_ms>=timeout_)return fail(Result::timeout);
    if(hold_&&role()&&!diagnostic_.link_initialized&&diagnostic_.elapsed_ms>=handshake_ms_)return fail(Result::timeout);
    if((hold_||watch_)&&!role()&&!agreed_){
        observe(now);
        if(served_activity_){served_activity_=false;last_activity_=now;}   // a served request is activity
        if(now-last_activity_>=idle_ms_)return fail(Result::timeout);
    }
    if(now-identity_ms_>=identity_interval_ms){if(!identity())return fail(Result::identity);identity_ms_=now;}
    if(!capture_state(diagnostic_.after))return fail(Result::identity);
    Result checked=check_open(diagnostic_.after);if(checked!=Result::ok)return fail(checked);
    if(!role()){
        // Snapshot once: an IRQ arrival between dispatch checks must wait for
        // the next poll, not bypass command/RPC handling into image transfer.
        const U32 queued=word(primary_+4);
        if(diagnostic_.after.protocol_state==1)diagnostic_.link_initialized=1;
        // b restarted alone. Never answer its bootloader: hold its first boot
        // request unanswered (a's bootloader answers it after a restarts) and serve nothing else.
        if(staged_&&!agreed_&&!pending_&&queued&&request_pool_slot(driver_,queued)&&
           !*reinterpret_cast<volatile unsigned short*>(queued+0x1c)&&
           boot_request(word(queued),byte(queued+5),word(queued+8),word(queued+0x18))&&
           (peer_restart_||(!rpc_&&!blob_&&!declined()&&server_.phase()==xyf_core::peer::Phase::waiting))){
            word(primary_+4)=0;                                   // taken, as stock 889f65c2, never answered
            if(!peer_restart_){peer_restart_=true;peer_ms_=now;}
            if(!peer_request_)peer_request_=queued;
            return Result::waiting;
        }
        if(peer_restart_&&staged_&&!agreed_)return Result::waiting;
        // Staged: a command from role1 (see ask()). Taken before any other use of the
        // pending request and answered here; the caller acts on commands_seen() before
        // its next poll, so commands and requests are handled in the order role1 sent them.
        if(staged_&&!agreed_&&!pending_){
            const U32 request=queued;
            if(request&&request_pool_slot(driver_,request)&&word(request)==0x11907694u&&
               !*reinterpret_cast<volatile unsigned short*>(request+0x1c)&&
               command_request(byte(request+5),word(request+8),word(request+0x18))){
                word(primary_+4)=0;pending_=request;                 // as stock 889f65c2: take, clear
                const U32 value=word(request+8);last_activity_=now;
                ++commands_seen_;last_command_=last_argument_=value;
                respond(command_ack(value));
                return Result::waiting;
            }
        }
        if(handshake_only_&&!agreed_){
            if(const U32 request=queued){
                if(!serve_)return fail(Result::protocol);            // no storage requests
                if(!request_pool_slot(driver_,request))return fail(Result::protocol);
                word(primary_+4)=0;++served_;served_activity_=true;   // as stock 889f65c2: take, clear, run
                serve_(serve_context_,request,*this);
                return Result::waiting;
            }
            if(!byte(primary_))return Result::waiting;
            if(!diagnostic_.link_initialized)return fail(Result::protocol);
            agreed_=true;diagnostic_.stage=Stage::draining;
        }
        if(!agreed_&&byte(primary_)){
            if(declined()||(staged_&&server_.phase()!=xyf_core::peer::Phase::release)){
                agreed_=true;diagnostic_.stage=Stage::draining;               // both boot embedded
            }
            else {
                U32 produced=0;xyf_core::peer::Request release={7,0,0};
                if(server_.process(release,scratch_,scratch_capacity_,produced)!=xyf_core::peer::Status::ok)return fail(Result::protocol);
                agreed_=true;committed_=server_.ready();diagnostic_.stage=Stage::draining;
            }
        }
        // Test 065: RPC mode in a staged session: storage requests to the serve hook while no
        // image or blob is frozen (never while a transfer is pending or running).
        if(!agreed_&&staged_&&rpc_&&serve_&&!blob_&&!pending_&&server_.phase()==xyf_core::peer::Phase::waiting){
            if(const U32 request=queued){
                if(!request_pool_slot(driver_,request))return fail(Result::protocol);
                word(primary_+4)=0;++served_;served_activity_=true;   // as stock 889f65c2: take, clear, run
                serve_(serve_context_,request,*this);
                return Result::waiting;
            }
        }
        if(!agreed_){
            if(!pending_){
                pending_=queued;
                if(pending_){if(!request_pool_slot(driver_,pending_))return fail(Result::protocol);word(primary_+4)=0;}
            }
            if(pending_){
                xyf_core::peer::Request req={byte(pending_+5),word(pending_+8),word(pending_+0x18)};
                if((req.operation!=0x11&&req.operation!=0x12&&req.operation!=0x18)||
                   word(pending_)!=(req.operation==0x18?0x11907694u:0x11907348u)||
                   *reinterpret_cast<volatile unsigned short*>(pending_+0x1c))return fail(Result::protocol);
                if(declined()){
                    // No image (or already refused): only a partition select may be
                    // answered without a data body; anything else is a protocol error.
                    if(req.operation!=0x18)return fail(Result::protocol);
                    declined_=true;respond(1);
                    return Result::waiting;
                }
                U32 produced=0;xyf_core::peer::Status status=server_.process(req,scratch_,scratch_capacity_,produced);
                if(status==xyf_core::peer::Status::waiting)return Result::choose_image;
                if(status==xyf_core::peer::Status::identity&&req.operation==0x18&&!req.argument0){
                    declined_=true;respond(2);return Result::waiting;      // restore veto
                }
                if(status!=xyf_core::peer::Status::ok)return fail(Result::protocol);
                word(pending_+0x10)=word(pending_+0x14)=0;
                if(req.operation!=0x18){word(pending_+0x20)=reinterpret_cast<U32>(scratch_);word(pending_+0x24)=produced;stock<0x889f4f10u,void>(scratch_,produced);}
                diagnostic_.last_operation=req.operation;++diagnostic_.responses;
                stock<0x889f6508u,void>(driver_,pending_);pending_=0;
                if(staged_&&blob_&&server_.phase()==xyf_core::peer::Phase::release){
                    // Blob restored: the session stays held; the server waits for
                    // the next freeze (the boot image).
                    server_.begin();frozen_size_=0;blob_=false;++blobs_;
                }
            }
        }
        if(!agreed_)return !declined()&&server_.phase()==xyf_core::peer::Phase::waiting?Result::choose_image:Result::waiting;
    } else {
        if(active_){
            if(!*(reinterpret_cast<volatile Byte*>(request_)+4))return Result::waiting;
            active_=false;U32 result=request_[4];diagnostic_.last_device_result=result;
            if(diagnostic_.stage==Stage::handshake1){
                if(result!=0xfedebedau)return fail(Result::protocol);
                word(0x20055004u)=(word(0x20055004u)&0xffcfffffu)|0x240000u;word(0x20055010u)=1;
                for(U32 i=0;i<12;++i)request_[i]=0;
                stock<0x889f4e82u,void>(request_,10,0x12345678);
                diagnostic_.stage=Stage::handshake10;active_=true;stock<0x11a0060cu,void>(driver_,request_);return Result::waiting;
            }
            if(diagnostic_.stage==Stage::handshake10){
                if(result!=0xedcba987u)return fail(Result::protocol);
                diagnostic_.link_initialized=1;diagnostic_.stage=Stage::receiving;
            }
            else if(handshake_only_){
                ++diagnostic_.responses;
                if(hold_&&diagnostic_.last_operation==10){
                    if(result!=expected_echo_)return fail(Result::protocol);
                    ++echoes_;
                }
                else if(diagnostic_.last_operation!=7||result)return fail(Result::protocol);
                else {agreed_=true;diagnostic_.stage=Stage::draining;}
            }
            else {
                ++diagnostic_.responses;
                if(filing_){filing_=false;return Result::waiting;}            // the file client reads it
                const xyf_core::peer::Phase phase=client_.phase();
                if(asking_){
                    asking_=false;
                    if(result!=expected_echo_)return fail(Result::protocol);    // role0's own answer (ask)
                    ++echoes_;
                }
                else if(staged_&&diagnostic_.last_operation==10){
                    if(result!=expected_echo_)return fail(Result::protocol);    // held command echo
                    ++echoes_;
                }
                else if(staged_&&!transferring_&&!declined_&&diagnostic_.last_operation==7){
                    if(result)return fail(Result::protocol);
                    agreed_=true;diagnostic_.stage=Stage::draining;             // release without image: embedded
                }
                else if(declined_){
                    if(diagnostic_.last_operation!=7||result)return fail(Result::protocol);
                    agreed_=true;diagnostic_.stage=Stage::draining;           // both boot embedded
                }
                else if(diagnostic_.last_operation==0x18&&result&&
                        (phase==xyf_core::peer::Phase::select||phase==xyf_core::peer::Phase::restore)){
                    declined_=true;client_.stop();                           // owner refused
                }
                else if(client_.accept(result,expected_bytes_?scratch_:0,expected_bytes_)!=xyf_core::peer::Status::ok)
                    return fail(Result::protocol);
                else if(client_.ready())committed_=true;                     // release 7 answered 0
            }
        }
        if(handshake_only_){
            if(!agreed_){
                if(active_||!diagnostic_.link_initialized||hold_)return Result::waiting;
                xyf_core::peer::Request release={7,0,0};return submit(release);
            }
        }
        else if(declined_){
            if(!agreed_){
                if(active_)return Result::waiting;
                xyf_core::peer::Request release={7,0,0};return submit(release);
            }
        }
        else {
        if(staged_&&blob_){
            if(client_.phase()==xyf_core::peer::Phase::admit&&client_.admit(client_.digest())!=xyf_core::peer::Status::ok)
                return fail(Result::identity);
            if(client_.phase()==xyf_core::peer::Phase::release){             // blob restored: back to held
                client_.stop();transferring_=false;blob_=false;blob_done_=true;++blobs_;
                return Result::waiting;
            }
        }
        if(client_.phase()==xyf_core::peer::Phase::admit){diagnostic_.stage=Stage::admission;return Result::admit_image;}
        if(client_.ready()){agreed_=true;diagnostic_.stage=Stage::draining;}
        if(!transferring_)return Result::waiting;
        if(!agreed_){xyf_core::peer::Request req;if(client_.request(req)!=xyf_core::peer::Status::ok)return fail(Result::protocol);return submit(req);}
        }
    }
    if(staged_&&!role()&&defer_close_)return Result::waiting;            // caller unmounts first
    checked=check_drained(diagnostic_.after);
    if(checked==Result::waiting)return Result::waiting;
    if(checked!=Result::ok)return fail(checked);
    return end();
}
Result Stock121PeerSession::end() {
    if(!opened_||!guarded_||owner!=reinterpret_cast<U32>(this)||!identity()||!logs_muted())return fail(Result::close);
    // Not agreed or not drained (e.g. a caller abandoning a held session): mark
    // the failure but still close, so no stock SPI handler stays registered
    // with this session's request object once the caller boots.
    if(!failed_){if(!agreed_||!capture(diagnostic_.after)||check_drained(diagnostic_.after)!=Result::ok)fail(Result::drain);}
    (role()?stock<0x889f562eu,void>(driver_):stock<0x889f5e38u,void>(driver_));opened_=false;
    // Restore the retained logger hooks once the transport is closed, even if
    // the closed-state check below fails: the boot continues either way.
    for(U32 i=0;i<4;++i)word(0x8803d06cu+4*i)=saved_logs_[i];
    guarded_=false;owner=0xffffffffu;
    if(!capture(diagnostic_.after)||check_closed(diagnostic_.after)!=Result::ok)return fail(Result::close);
    if(failed_)return diagnostic_.result;
    diagnostic_.stage=Stage::closed;diagnostic_.result=Result::ready;return Result::ready;
}
// Role0 also counts as committed when its poll stopped (timeout/failure)
// after a completed transfer and restore but the stock ISR had already
// accepted role1's release 7 (the byte survives the close): role1 holds a
// zero release result and boots the file image, so role0 must too.
bool Stock121PeerSession::committed() const {
    if(committed_)return true;
    return !role()&&frozen_size_&&!declined()&&primary_&&
           server_.phase()==xyf_core::peer::Phase::release&&byte(primary_)==1;
}
U32 Stock121PeerSession::stale_deferred(){return stale_deferred_node;}
U32 Stock121PeerSession::stale_deferred_last(){return stale_deferred_latest;}
#else
bool Stock121PeerSession::capture(Snapshot&){return false;}
bool Stock121PeerSession::capture_state(Snapshot&){return false;}
U32 Stock121PeerSession::stale_deferred(){return 0;}
U32 Stock121PeerSession::stale_deferred_last(){return 0;}
Result Stock121PeerSession::begin(services::Phase,Byte*,U32,Byte*,U32,U32,U32){return fail(Result::unavailable);}
Result Stock121PeerSession::submit(const xyf_core::peer::Request&){return fail(Result::unavailable);}
void Stock121PeerSession::observe(U32){}
void Stock121PeerSession::respond(U32){}
void Stock121PeerSession::reply(U32,U32,U32){}
bool Stock121PeerSession::committed() const { return committed_; }
Result Stock121PeerSession::command(U32){return Result::unavailable;}
Result Stock121PeerSession::ask(U32){return Result::unavailable;}
bool Stock121PeerSession::answering() const{return false;}
Result Stock121PeerSession::release(){return Result::unavailable;}
U32* Stock121PeerSession::request_slot(){return 0;}
Result Stock121PeerSession::send_request(U32){return Result::unavailable;}
Result Stock121PeerSession::poll(U32){return fail(Result::unavailable);}
Result Stock121PeerSession::end(){return fail(Result::unavailable);}
#endif
} }
