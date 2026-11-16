#include "cold_heap.hpp"
namespace xy_forever { namespace cold_heap {
namespace {
bool read(const Memory& m, U32 address, U32& value) {
    return m.word && m.word(m.user,address,value);
}
}
Result inspect(const Memory& m, Snapshot& s) {
    s.context=s.count=s.base=s.extent=s.brk=s.next_context=s.cursor=s.free_head=0;
    s.free_nodes=s.highest_free_end=0;
    U32 table_base=0, table_size=0, table_id=0, table_end=0, id=0;
    if (!read(m,0x889ebaa0u,s.context) || !read(m,0x889ebaa4u,s.count) ||
        !read(m,0x88a2a4d0u,table_base) || !read(m,0x88a2a4d4u,table_size) ||
        !read(m,0x88a2a4d8u,table_id) || !read(m,0x88a2a4dcu,table_end)) return Result::read;
    if (s.context!=arena_context || s.count!=1 || table_base!=arena_context ||
        table_size!=0x01fffffcu || table_id || table_end) return Result::layout;
    if (!read(m,s.context,s.base) || !read(m,s.context+4,s.extent) ||
        !read(m,s.context+8,id) || !read(m,s.context+12,s.brk) ||
        !read(m,s.context+16,s.next_context) || !read(m,s.context+20,s.cursor) ||
        !read(m,s.context+24,s.free_head)) return Result::read;
    if (s.base!=arena_base || s.extent!=arena_extent || id || s.next_context ||
        s.brk<s.base || s.brk>arena_end || (s.brk&7)) return Result::layout;
    if (s.brk>staging_floor) return Result::staging_overlap;
    bool cursor_found=!s.cursor || s.cursor==s.context+24;
    U32 node=s.free_head, previous_end=s.base;
    while (node) {
        if (s.free_nodes==max_free_nodes) return Result::capacity;
        // Check bounds before reading a node, including space for its next link.
        if ((node&7) || node<s.base || node<previous_end ||
            node>s.brk || s.brk-node<16) return Result::free_list;
        U32 bytes=0, heap_id=0, next=0;
        if (!read(m,node,bytes) || !read(m,node+4,heap_id) || !read(m,node+8,next)) return Result::read;
        if (bytes<16 || (bytes&7) || bytes>s.brk-node || heap_id ||
            (next && (next<=node || next<node+bytes))) return Result::free_list;
        if (s.cursor==node+8) cursor_found=true;
        previous_end=node+bytes; s.highest_free_end=previous_end;
        ++s.free_nodes; node=next;
    }
    return cursor_found ? Result::ok : Result::cursor;
}
#if defined(__bfin__)
namespace {
bool native_word(void*, U32 address, U32& value) {
    value=*reinterpret_cast<volatile U32*>(address); return true;
}
}
bool guard(Snapshot& output, Result& result) {
    const Memory memory={0,native_word}; result=inspect(memory,output); return result==Result::ok;
}
#else
bool guard(Snapshot&, Result& result) { result=Result::unavailable; return false; }
#endif
} }
