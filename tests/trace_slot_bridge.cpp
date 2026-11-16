#include "../native/trace_slot.hpp"
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <fstream>
#include <iterator>
#include <vector>

static std::vector<unsigned char> read(const char* path) {
    std::ifstream f(path, std::ios::binary);
    if (!f) std::exit(2);
    return std::vector<unsigned char>(std::istreambuf_iterator<char>(f), {});
}
int main(int argc, char** argv) {
    if (argc != 8) return 2;
    const auto original = read(argv[1]), expected = read(argv[2]), replacement = read(argv[3]);
    const unsigned target = unsigned(std::strtoul(argv[5], 0, 0));
    const unsigned count = unsigned(std::strtoul(argv[6], 0, 0));
    if (count > expected.size() || count > replacement.size()) return 2;
    std::vector<unsigned char> buffer(4096 + original.size() + 4096);
    unsigned char* image = buffer.data() + 4096;
    std::memcpy(image, original.data(), original.size());
    const unsigned char *preimage = expected.data(), *next = replacement.data();
    unsigned char* input = image;
    xyf_core::Range allowed[] = {{0x1000, 0x10000}};
    xyf_core::Range stock_allowed[] = {{0x08000000, 0x08040000},
                                    {0x11800000, 0x11c00000}, {0x80000000, 0x90000000}};
    xyf_core::Range stock_protected[] = {{0x0803e0c0, 0x0803e142}, {0x11803000, 0x11804000},
        {0x8803d000, 0x8803d02a}, {0x88a346a6, 0x88a3481c}, {0x88a34aec, 0x88a34d6c},
        {0x8a000000, 0x8a800000}, {0x8ab00000, 0x8ab10000}, {0x8b000000, 0x8b800000}};
    xyf_core::Range protected_range[] = {{target, target + count}};
    const char* mode = argv[7];
    if (!std::strcmp(mode, "alias_start")) next = image;
    if (!std::strcmp(mode, "alias_end")) next = image + original.size() - 1;
    if (!std::strcmp(mode, "alias_prefix")) next = image - 1;
    if (!std::strcmp(mode, "alias_after")) {
        next = image + original.size();
        std::memcpy(image + original.size(), replacement.data(), count);
    }
    if (!std::strcmp(mode, "null_image")) input = 0;
    if (!std::strcmp(mode, "null_expected")) preimage = 0;
    if (!std::strcmp(mode, "null_replacement")) next = 0;
    const bool protect = !std::strcmp(mode, "protect");
    const bool stock = !std::strcmp(mode, "stock");
    const xyf_core::Policy policy = {stock ? stock_allowed : allowed, stock ? 3u : 1u,
        stock ? stock_protected : protected_range, stock ? 8u : protect ? 1u : 0u};
    const auto result = xyf_core::trace_slot::replace(input, unsigned(original.size()), policy,
                                                   target, preimage, next, count);
    std::ofstream output(argv[4], std::ios::binary);
    output.write(reinterpret_cast<const char*>(image), original.size());
    if (!output) return 2;
    std::printf("%s %u %s\n", xyf_core::trace_slot::status_name(result.status),
                result.file_offset, xyf_core::error_name(result.validation_error));
    return 0;
}
