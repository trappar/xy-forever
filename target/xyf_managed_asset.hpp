#ifndef XYF_MANAGED_ASSET_HPP
#define XYF_MANAGED_ASSET_HPP
#include "managed_blob_link.hpp"
#include "xyf_blob.hpp"

// Prepare an alias of the manifest's blob. A previously verified, sealed blob and its kept name
// identify the same filesystem object. If that association is missing (legacy entry or replacement),
// verify the bytes before admitting a new association. One scratch alias and one copy at most.
namespace xyf { namespace managed_asset {
namespace ml = xy_forever::managed_blob_link;
struct File {
    uint32_t device, inode, size;
};
static inline bool same_file(const File& a, const File& b) {
    return a.inode && a.inode == b.inode && a.device == b.device && a.size == b.size;
}
enum Result : int { more = 0, ready = 1, storage = -1, damaged = -2 };
enum Stage : uint32_t {
    stat_kept,
    clear_alias,
    stage_alias,
    stat_alias,
    clear_copy,
    copy_missing,
    clear_seal,
    open_seal,
    copy_seal,
    open_check,
    read_check,
    close_check,
    publish_seal
};
struct Asset {
    blob::Copy copy;
    char alias[240], seal[240];
    const uint8_t* hex;
    File kept, staged;
    Stage stage;
    bool verified, sealing, bound;
};

static inline bool begin(Asset& a, const char* kept, const uint8_t* hex) {
    a.hex = hex;
    a.stage = stat_kept;
    a.verified = a.sealing = a.bound = false;
    a.copy.input = a.copy.output = -1;
    a.alias[0] = a.seal[0] = a.copy.temporary[0] = 0;
    return blob::path(a.copy.source, kept, "");
}

template<class Io> static Result step(Asset& a, Io& io) {
    auto& c = a.copy;
    switch (a.stage) {
    case stat_kept: {
        if (io.stat_asset(c.source, a.kept) != 0) return storage;
        uint8_t digest[32];
        for (uint32_t i = 0; i != 32; ++i) {
            const uint8_t hi = a.hex[2 * i], lo = a.hex[2 * i + 1];
            digest[i] = uint8_t((hi <= '9' ? hi - '0' : hi - 'a' + 10) << 4 | (lo <= '9' ? lo - '0' : lo - 'a' + 10));
        }
        char temporary[240];
        blob::scratch_name(temporary, a.kept.size);
        if (!blob::begin(c, c.source, temporary, digest)) return storage;
        ml::temporary(a.alias, a.kept.size);
        ml::temporary(a.seal, a.kept.size, true);
        a.stage = clear_alias;
        return more;
    }
    case clear_alias:
        if (io.unlink(a.alias) < 0) return storage;
        a.stage = stage_alias;
        return more;
    case stage_alias: {
        const int r = io.stage_blob_link(a.alias, a.hex);
        if (r < 0 || (r && a.verified)) return storage;
        a.stage = r == 0 ? stat_alias : r == 1 ? clear_copy : clear_seal;
        return more;
    }
    case stat_alias:
        if (io.stat_asset(a.alias, a.staged) != 0) return storage;
        if (a.staged.size != a.kept.size) return damaged;
        a.bound = same_file(a.kept, a.staged);
        if (a.bound || a.verified) return ready;
        a.stage = open_check;
        return more;
    case clear_copy:
        if (io.unlink(c.temporary) < 0) return storage;
        a.stage = copy_missing;
        return more;
    case copy_missing: {
        const int r = blob::step(c, io);
        if (r < 0) return damaged;
        if (r) a.stage = clear_seal;
        return more;
    }
    case clear_seal:
        if (io.unlink(a.seal) < 0) return storage;
        a.stage = open_seal;
        return more;
    case open_seal:
        if (io.stage_blob_link(a.seal, a.hex, true) != 0) return storage;
        a.stage = copy_seal;
        return more;
    case copy_seal: {
        const int r = io.copy_blob_piece();
        if (r < 0) return damaged;
        if (r) {
            a.sealing = true;
            a.stage = open_check;
        }
        return more;
    }
    case open_check:
        c.input = io.open(a.sealing ? a.seal : a.alias, false);
        if (c.input < 0) return storage;
        c.hash.reset();
        a.stage = read_check;
        return more;
    case read_check: {
        const int n = io.read(c.input, c.buffer, sizeof(c.buffer));
        if (n < 0 || n > int(sizeof(c.buffer))) return storage;
        if (n) return c.hash.update(c.buffer, uint32_t(n)) ? more : damaged;
        a.stage = close_check;
        return more;
    }
    case close_check: {
        const int r = io.close(c.input);
        c.input = -1;
        if (r) return storage;
        if (!blob::matches(c)) return damaged;
        if (!a.sealing) return ready;
        a.stage = publish_seal;
        return more;
    }
    case publish_seal:
        if (io.publish_blob_link(a.seal, c.target) < 0) return storage;
        a.verified = true;
        a.stage = stage_alias;
        return more;
    }
    return storage;
}

template<class Io> static void abandon(Asset& a, Io& io) {
    if (io.lost()) return;
    blob::abandon(a.copy, io);
    if (a.alias[0]) io.unlink(a.alias);
    if (a.seal[0]) io.unlink(a.seal);
    if (a.copy.temporary[0]) io.unlink(a.copy.temporary);
}
}}
#endif
