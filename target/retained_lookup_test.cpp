#include "retained_lookup.hpp"
#include <assert.h>
#include <stdio.h>
#include <string.h>
#include <vector>
using namespace xy_forever::retained_lookup;
namespace {
const U32 head = 0x1000, objects = 0x2000, strings = 0x50000;
struct Model {
    std::vector<Byte> bytes;
    Range ranges[3];
    U32 count, reads, fail_at;
    Model(U32 nodes = 1) : bytes(0x60000, 0), count(nodes), reads(0), fail_at(0) {
        ranges[0] = {head, head + 8}; ranges[1] = {objects, strings};
        ranges[2] = {strings, U32(bytes.size())};
        put(head, nodes ? node(0) : head); put(head + 4, nodes ? node(nodes - 1) : head);
        for (U32 i = 0; i != nodes; ++i) {
            put(object(i), strings + i * 128);
            put(node(i), i + 1 == nodes ? head : node(i + 1));
            put(node(i) + 4, i ? node(i - 1) : head);
            name(i, i ? "/other" : "/yaffs2");
        }
    }
    U32 object(U32 i) const { return objects + i * 4096; }
    U32 node(U32 i) const { return object(i) + node_offset; }
    void put(U32 address, U32 value) {
        for (U32 i = 0; i != 4; ++i) bytes[address + i] = Byte(value >> (i * 8));
    }
    void name(U32 i, const char* value) { strcpy(reinterpret_cast<char*>(&bytes[strings + i * 128]), value); }
    static bool read(void* user, U32 address, Byte* output, U32 count) {
        Model& m = *static_cast<Model*>(user);
        ++m.reads;
        if (m.fail_at && m.reads == m.fail_at) return false;
        assert(address <= m.bytes.size() && count <= m.bytes.size() - address);
        memcpy(output, &m.bytes[address], count); return true;
    }
    Memory memory() { return {this, read, ranges, 3, {objects, strings}}; }
    Result find(const char* query, U32& result, Diagnostic& d) {
        return find_exact(memory(), head, query, result, d);
    }
};
void expected(Model& m, Result wanted, const char* query = "/yaffs2") {
    U32 device = 0xfeed1234; Diagnostic d;
    assert(m.find(query, device, d) == wanted);
    assert((wanted == Result::ok) ? device == m.object(0) : device == 0);
    assert(d.visited <= max_nodes && d.name_bytes <= max_nodes * name_capacity);
    assert(m.reads <= 2 + max_nodes * (3 + name_capacity));
}
}
int main() {
    { Model m; expected(m, Result::ok); }
    { Model m(2); m.name(0, "///yaffs2///"); expected(m, Result::ok, "yaffs2/"); }
    { Model m(0); expected(m, Result::missing); }
    { Model m; expected(m, Result::missing, "/YAFFS2"); }
    { Model m(2); m.name(1, "yaffs2"); expected(m, Result::duplicate); }
    { Model m; m.put(m.node(0), 0); expected(m, Result::pointer); }
    { Model m; m.put(head, m.node(0) + 1); expected(m, Result::pointer); }
    { Model m; m.put(m.node(0) + 4, 0); expected(m, Result::links); }
    { Model m; m.put(head + 4, head); expected(m, Result::links); }
    { Model m; m.put(m.node(0), m.node(0)); expected(m, Result::cycle); }
    { Model m(3); m.put(m.node(2), m.node(1)); expected(m, Result::cycle); }
    { Model m; m.put(m.object(0), 0x20000000); expected(m, Result::read); }
    { Model m; memset(&m.bytes[strings], 'a', name_capacity); expected(m, Result::name); }
    { Model m; m.bytes[strings] = 0xff; expected(m, Result::name); }
    { Model m; m.fail_at = 5; expected(m, Result::read); }
    { Model m; m.ranges[1].end = objects + 4; expected(m, Result::read); }
    { Model m(max_nodes); expected(m, Result::ok); }
    { Model m(max_nodes + 1); expected(m, Result::capacity); }
    { Model m; expected(m, Result::argument, "/"); }
    { Model m; char query[name_capacity]; memset(query, 'a', sizeof(query)); expected(m, Result::argument, query); }
    { Model m; Memory io = m.memory(); io.readable_count = 17; U32 dev = 1; Diagnostic d;
      assert(find_exact(io, head, "/yaffs2", dev, d) == Result::argument && !dev && !m.reads); }
    { U32 dev = 1; assert(stock121_yaffs_device(dev) == Result::unavailable && !dev); }
    puts("22 retained lookup checks passed");
}
