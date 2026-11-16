#include "../target/peer_services.hpp"
#include <new>
using namespace xy_forever;
extern "C" {
unsigned int peer_services_dma_range(unsigned int address,unsigned int count){return peer_services::dma_buffer_range(address,count);}
unsigned int peer_services_request_slot(unsigned int driver,unsigned int request){return peer_services::request_pool_slot(driver,request);}
unsigned int peer_services_check(unsigned int which,const peer_services::Snapshot* s){
    return unsigned(which==0?peer_services::check_closed(*s):which==1?peer_services::check_open(*s):
                    which==3?peer_services::check_quiet(*s):peer_services::check_drained(*s));
}
unsigned int peer_services_command_request(unsigned int op,unsigned int a0,unsigned int a1){return peer_services::command_request(op,a0,a1);}
unsigned int peer_services_command_ack(unsigned int value){return peer_services::command_ack(value);}
unsigned int peer_services_size(){return sizeof(peer_services::Stock121PeerSession);}
unsigned int peer_services_diagnostic_size(){return sizeof(peer_services::Diagnostic);}
void peer_services_construct(void* p,peer_services::Diagnostic* d){new(p)peer_services::Stock121PeerSession(*d);}
unsigned int peer_services_begin_host(void* p){
    return unsigned(static_cast<peer_services::Stock121PeerSession*>(p)->begin(services::Phase::cold_after_teardown,0,65536,0,65536,0,1000));
}
unsigned int peer_services_ready(void* p){return static_cast<peer_services::Stock121PeerSession*>(p)->safe_to_handoff();}
}
