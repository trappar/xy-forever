#include "../target/xyf_stage2.hpp"
extern "C" unsigned stage2_validate(const unsigned char* data, unsigned length, unsigned stage1) {
    xyf::stage2::Header h;
    return unsigned(xyf::stage2::validate(data, length, stage1, h));
}
extern "C" unsigned stage2_accept(const unsigned char* data, unsigned length, unsigned stage1) {
    xyf::stage2::Header h;
    return unsigned(xyf::stage2::accept(data, length, stage1, h));
}
extern "C" unsigned stage2_constant(unsigned which) {
    using namespace xyf::stage2;
    const unsigned values[] = {window_begin, window_end, max_image, header_bytes, abi, this_stage1,
                               exit_continue, exit_menu, event_key, event_link_lost};
    return which < sizeof(values) / sizeof(values[0]) ? values[which] : 0xdeadbeefu;
}
extern "C" const char* stage2_path() { return xyf::stage2::path; }
extern "C" unsigned stage2_classify(const unsigned char* data, unsigned length, unsigned stage1, unsigned* detail) {
    xyf::stage2::Header h;
    uint32_t d = 0;
    const unsigned out = unsigned(xyf::stage2::classify(data, length, stage1, h, d));
    *detail = d;
    return out;
}
extern "C" unsigned stage2_after(unsigned exit, unsigned link_lost) {
    return unsigned(xyf::stage2::after(exit, link_lost != 0));
}
extern "C" unsigned stage2_decide(unsigned exit, unsigned link_lost, unsigned count, unsigned features,
                                  unsigned* entry) {
    uint32_t e = 0xdeadu;
    const unsigned out = unsigned(xyf::stage2::decide(exit, link_lost != 0, count, features, e));
    *entry = e;
    return out;
}
