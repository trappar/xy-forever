// native/hash.hpp: a finished SHA-256 state refuses more input.
#include "doctest.h"
#include "hash.hpp"

TEST_CASE("a finished sha256 state refuses updates and a second finish") {
    xyf_core::hash::Sha256 state;
    xyf_core::hash::Digest digest;
    REQUIRE(state.update(reinterpret_cast<const unsigned char*>("abc"), 3));
    REQUIRE(state.finish(digest));
    CHECK(digest.bytes[0] == 0xba);
    CHECK_FALSE(state.update(nullptr, 0));
    CHECK_FALSE(state.finish(digest));
}
