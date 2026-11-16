#ifndef XYF_PACKAGE_POLICY_HPP
#define XYF_PACKAGE_POLICY_HPP
#include "ldr.hpp"
namespace xyf_core { namespace package {
enum class Role { os, backplane, ble, metadata, content, excluded_bootloader, excluded_host };
enum class Status { ok, unsafe_name, unknown_member, capacity, version };
struct Destination { Role role; char relative[257]; };
// Only relative generation paths are emitted; no boot-partition or /os.ldr path.
Status classify(const char* name, U32 length, Destination& destination);
struct Version { U32 field[4]; };
Status parse_version(const Byte* bytes, U32 size, Version& version);
int compare(const Version& a, const Version& b);
} }
#endif
