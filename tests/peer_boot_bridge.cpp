#include "../native/peer_boot.hpp"
#include <new>
using namespace xyf_core;
extern "C" {
int peer_encode_command(U32 operation,U32 a,U32 b,U32 sequence,Byte* out,U32 cap,U32* n){peer::Request r={operation,a,b};return int(peer::encode_command(r,sequence,out,cap,*n));}
int peer_decode_command(const Byte* in,U32 n,U32* op,U32* a,U32* b,U32* sequence){peer::Request r;int status=int(peer::decode_command(in,n,r,*sequence));*op=r.operation;*a=r.argument0;*b=r.argument1;return status;}
int peer_encode_result(U32 op,U32 seq,U32 result,Byte* out,U32 cap,U32* n){return int(peer::encode_result(op,seq,result,out,cap,*n));}
int peer_decode_result(const Byte* in,U32 n,U32 op,U32 seq,U32* result){return int(peer::decode_result(in,n,op,seq,*result));}
U32 peer_server_size(){return sizeof(peer::Server);}
U32 peer_client_size(){return sizeof(peer::Client);}
void peer_server_construct(void* p){new(p) peer::Server;}
void peer_client_construct(void* p){new(p) peer::Client;}
void peer_server_begin(void* p){static_cast<peer::Server*>(p)->begin();}
int peer_server_freeze(void* p,const Byte* b,U32 n,const Byte* d){
    hash::Digest digest;for(U32 i=0;i<32;++i)digest.bytes[i]=d[i];
    return int(static_cast<peer::Server*>(p)->freeze(b,n,digest));
}
int peer_server_freeze_hashing(void* p,const Byte* b,U32 n,Byte* d){
    hash::Digest digest;const int status=int(static_cast<peer::Server*>(p)->freeze_hashing(b,n,digest));
    for(U32 i=0;i<32;++i)d[i]=digest.bytes[i];return status;
}
int peer_server_process(void* p,U32 operation,U32 a,U32 b,Byte* out,U32 cap,U32* n){
    peer::Request request={operation,a,b};return int(static_cast<peer::Server*>(p)->process(request,out,cap,*n));
}
int peer_server_phase(void* p){return int(static_cast<peer::Server*>(p)->phase());}
int peer_server_ready(void* p){return static_cast<peer::Server*>(p)->ready();}
int peer_client_init(void* p,Byte* b,U32 n){return int(static_cast<peer::Client*>(p)->initialize(b,n));}
unsigned peer_client_crc(void* p){return static_cast<peer::Client*>(p)->crc();}
int peer_client_init_unhashed(void* p,Byte* b,U32 n){return int(static_cast<peer::Client*>(p)->initialize(b,n,false));}
int peer_server_freeze_unhashed(void* p,const Byte* b,U32 n,U32 crc){return int(static_cast<peer::Server*>(p)->freeze_unhashed(b,n,crc));}
int peer_client_request(void* p,U32* op,U32* a,U32* b){
    peer::Request r;int s=int(static_cast<peer::Client*>(p)->request(r));*op=r.operation;*a=r.argument0;*b=r.argument1;return s;
}
int peer_client_accept(void* p,U32 result,const Byte* b,U32 n){return int(static_cast<peer::Client*>(p)->accept(result,b,n));}
int peer_client_admit(void* p,const Byte* d){hash::Digest digest;for(U32 i=0;i<32;++i)digest.bytes[i]=d[i];return int(static_cast<peer::Client*>(p)->admit(digest));}
int peer_client_phase(void* p){return int(static_cast<peer::Client*>(p)->phase());}
int peer_client_ready(void* p){return static_cast<peer::Client*>(p)->ready();}
U32 peer_client_image_size(void* p){return static_cast<peer::Client*>(p)->image_size();}
void peer_client_digest(void* p,Byte* out){const hash::Digest& digest=static_cast<peer::Client*>(p)->digest();for(U32 i=0;i<32;++i)out[i]=digest.bytes[i];}
}
