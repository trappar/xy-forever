#ifndef XYF_INPUT_HPP
#define XYF_INPUT_HPP
#include "ldr.hpp"
namespace xyf_core { namespace input {
struct Reader {
    void* context;
    U32 size;
    bool (*read)(void*, U32 offset, Byte* destination, U32 count);
};
enum class Format { unknown, tfw, zip, ldr };
inline Format identify(const Byte* prefix, U32 size) {
    if (!prefix || size<4) return Format::unknown;
    if ((prefix[0]==0xba && prefix[1]==0xbe && prefix[2]==0xca && prefix[3]==0xfe) ||
        (prefix[0]==0xbe && prefix[1]==0xef && prefix[2]==0xca && prefix[3]==0xfe)) return Format::tfw;
    if (prefix[0]=='P' && prefix[1]=='K' &&
        ((prefix[2]==3 && prefix[3]==4)||(prefix[2]==5 && prefix[3]==6))) return Format::zip;
    if (size>=16 && prefix[3]==0xad && prefix[0]==4 && prefix[1]==0x50) return Format::ldr;
    return Format::unknown;
}
// Bounds-checked read through the reader (defined in tfw.cpp: out of line and not weak, so a
// firmware build calls it directly, which its call audit can resolve).
bool read(const Reader& reader,U32 offset,Byte* destination,U32 count);
} }
#endif
