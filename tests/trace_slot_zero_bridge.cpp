#include "../native/trace_slot.hpp"
#include <cstdio>
#include <cstdlib>
#include <fstream>
#include <iterator>
#include <vector>
int main(int argc, char** argv) {
    if (argc != 4) return 2;
    std::ifstream f(argv[1], std::ios::binary);
    if (!f) return 2;
    std::vector<unsigned char> image((std::istreambuf_iterator<char>(f)), {});
    const std::vector<unsigned char> before = image;
    xyf_core::Range allowed[] = {{0x1000, 0x400000}};
    const xyf_core::Policy policy = {allowed, 1, 0, 0};
    const auto result = xyf_core::trace_slot::locate_zero(
        image.data(), unsigned(image.size()), policy,
        unsigned(std::strtoul(argv[2], 0, 0)), unsigned(std::strtoul(argv[3], 0, 0)));
    if (image != before) return 3;   // never modifies the image
    std::printf("%s %u\n", xyf_core::trace_slot::status_name(result.status), result.file_offset);
    return 0;
}
