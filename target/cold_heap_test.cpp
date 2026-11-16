#include "cold_heap.hpp"
#include <assert.h>
#include <map>
#include <stdio.h>
using namespace xy_forever::cold_heap;
struct Model {
    std::map<U32,U32> words;
    U32 reads;
    Model():reads(0) {
        words[0x889ebaa0]=arena_context; words[0x889ebaa4]=1;
        words[0x88a2a4d0]=arena_context; words[0x88a2a4d4]=0x1fffffc;
        words[0x88a2a4d8]=words[0x88a2a4dc]=0;
        words[arena_context]=arena_base; words[arena_context+4]=arena_extent;
        words[arena_context+8]=0; words[arena_context+12]=arena_base;
        words[arena_context+16]=words[arena_context+20]=words[arena_context+24]=0;
    }
    static bool read(void* user,U32 address,U32& value) {
        Model& m=*static_cast<Model*>(user); ++m.reads;
        std::map<U32,U32>::iterator at=m.words.find(address);
        if(at==m.words.end())return false;
        value=at->second;return true;
    }
    Result run(Snapshot& s) { const Memory m={this,read};return inspect(m,s); }
    void free_node(U32 address,U32 size,U32 next) {
        words[address]=size;words[address+4]=0;words[address+8]=next;
    }
};
int main() {
    Snapshot s; Model m;
    assert(m.run(s)==Result::ok && s.brk==arena_base && !s.free_nodes);
    m.words[arena_context+12]=arena_base+512;
    m.words[arena_context+24]=arena_base;
    m.words[arena_context+20]=arena_base+8;
    m.free_node(arena_base,32,arena_base+128);
    m.free_node(arena_base+128,64,0);
    assert(m.run(s)==Result::ok && s.free_nodes==2 && s.highest_free_end==arena_base+192);
    Model bad=m; bad.words[0x889ebaa0]=0xdeadbeef;
    assert(bad.run(s)==Result::layout && bad.reads-m.reads==6);
    bad=m;bad.words[arena_context+12]=staging_floor+8;
    bad.words[arena_context+24]=0xdeadbeef;
    assert(bad.run(s)==Result::staging_overlap && bad.reads-m.reads==13);
    bad=m;bad.words[arena_context+24]=arena_base+1;
    assert(bad.run(s)==Result::free_list);
    bad=m;bad.words[arena_base]=0xfffffff8;
    assert(bad.run(s)==Result::free_list);
    bad=m;bad.words[arena_base+4]=1;
    assert(bad.run(s)==Result::free_list);
    bad=m;bad.words[arena_base+8]=arena_base;
    assert(bad.run(s)==Result::free_list);
    bad=m;bad.words[arena_base+8]=arena_base+16;
    assert(bad.run(s)==Result::free_list);
    bad=m;bad.words[arena_context+20]=arena_base+12;
    assert(bad.run(s)==Result::cursor);
    bad=m;bad.words.erase(arena_base+8);
    assert(bad.run(s)==Result::read);
    bad=m;bad.words[arena_context+12]=staging_floor;
    assert(bad.run(s)==Result::ok);
    Model many;
    many.words[arena_context+24]=arena_base;
    many.words[arena_context+12]=arena_base+(max_free_nodes+1)*16;
    for(U32 i=0;i<=max_free_nodes;++i)
        many.free_node(arena_base+i*16,16,i==max_free_nodes?0:arena_base+(i+1)*16);
    assert(many.run(s)==Result::capacity && s.free_nodes==max_free_nodes && many.reads==13+3*max_free_nodes);
    Result result=Result::ok;
    assert(!guard(s,result) && result==Result::unavailable);
    puts("14 cold heap guard checks passed");
}
