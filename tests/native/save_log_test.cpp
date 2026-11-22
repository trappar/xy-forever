// target/xyf_save_log.hpp: the save-time log's laps, line and merge (the last 20 records). tests/test_save_log.py
// reads `want` below for xyf_host.py save-times' table.
#include "doctest.h"
#include "xyf_save_log.hpp"
#include <stdio.h>
#include <string>
using namespace xyf::save_log;

TEST_CASE("save log line and merge") {
    const char want[] =
        "kind=save result=ok mods=30 memory=expanded added=1 removed=0 total=2205 list=200 check=5 base=0 packages=0 "
        "catalog=0 stock=0 patch1=0 link1=1000 zero=0 link2=1000 set_module=0 patch2=0 crc=0 content=0 write=0 verify=0 "
        "image_check=0 finish=0 away=0 in=0 clean=0 close=0 stamp=0 switch=0 switch_close=0 config=0 entry_files=0 "
        "compared=0 read=0 written=7 removed_files=0 bytes=4096000000\n";
    Times t;
    begin(t, k_save, 1000, p_list);
    lap(t, 1200, p_check);                 // list 200
    lap(t, 1205, p_job_base + 5);          // check 5, then link1
    lap(t, 2205, p_job_base + 7);          // link1 1000, then link2
    t.mods = 30;
    t.expanded = 1;
    t.added = 1;
    t.ok = 1;
    t.written = 7;
    t.bytes = 4096000000u;
    static char text[max_text + 64];
    static char line[max_line];
    const unsigned n = format(line, sizeof(line), t, 3205);   // link2 1000 (running)
    CHECK(std::string(line, n) == want);
    unsigned length = 0;
    std::string last20;
    for (unsigned i = 0; i != 25; ++i) {
        char one[16];
        const int k = snprintf(one, sizeof(one), "kind=save n=%u\n", i);
        length = merge(text, length, one, unsigned(k));
        if (i >= 5) last20 += one;
    }
    CHECK(std::string(text, length) == last20);                 // the last 20
    CHECK(format(line, 40, t, 3205) == 0);                     // too small: 0
}
