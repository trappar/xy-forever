#ifndef XYF_TFW_HASH_RPC_HPP
#define XYF_TFW_HASH_RPC_HPP
#include <stdint.h>
namespace xy_forever { namespace tfw_hash_rpc {
// A's keyless SHA-256 job, using stock path-class access (0x21).
static const uint32_t version = 1, tag = 0x01544800u, tag_mask = 0x00ffff00u;
static const uint32_t start = 1, status = 2, cancel = 3, digest = 0x10, digest_words = 8;
static const uint32_t lease_ms = 10000;
// All replies carry a status in value. On success start and status return the exact
// package size in error, busy status returns bytes read, and digest+n returns word n
// of SHA-256 (little-endian). An unsupported version returns A's version in error.
}}
#endif
